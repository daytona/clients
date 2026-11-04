# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Pair exact literal unpacking targets before any target mutates the environment."""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable

from .annotation_exports import annotation_alias, joined_bindings
from .annotation_proofs import AnnotationProofs
from .attribute_proofs import AttributeProofs
from .bindings import indirect_writes
from .context_managers import context_entry
from .fallback_proofs import nonnull_client, required_client_annotation
from .field_effects import CLIENT_ANNOTATION, escape_fields
from .functionflow import FunctionVisitor
from .proof_mutations import CONTAINER_WRITE
from .resolution import dotted


def annotated_assignment(
    node: ast.AnnAssign | ast.Assign, inferred: str, annotated: str
) -> str:
    """Trust SDK client annotations for unresolved values, not concrete conflicts."""
    if (
        isinstance(node, ast.AnnAssign)
        and node.value is not None
        and not isinstance(node.target, ast.Name)
    ):
        return inferred
    if (
        annotated in {"client", "unproven:client"}
        and isinstance(node.value, ast.Name)
        and inferred.startswith(("module:", "local-module:"))
    ):
        return "unproven:client"
    if annotated in {"client", "unproven:client"} and (
        not inferred or inferred.startswith("module:") or inferred == "unproven:client"
    ):
        return annotated
    return inferred or annotated


class AssignmentVisitor(FunctionVisitor):
    """Evaluate assignment/context inputs before storing their receiver proofs."""

    binding: str
    declarations: dict[str, str]

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if (
            not self.scope
            and not self.in_class_body
            and node.value is not None
            and isinstance(node.target, ast.Name)
            and self.resolver.infer(node.annotation, self.annotation_env())
            in {"module:typing.TypeAlias", "module:typing_extensions.TypeAlias"}
        ):
            self.visit(node.value)
            self.bind(
                node.target,
                annotation_alias(
                    node.value,
                    AnnotationProofs(self.resolver, {**self.env, **self.type_bindings}),
                ),
            )
            return
        annotated = self.resolver.annotation(node.annotation, self.annotation_env())
        root, separator, member = dotted(node.target).rpartition(".")
        if separator and annotated == "client":
            self.env[root + CLIENT_ANNOTATION + member] = "client"
        if isinstance(node.target, ast.Name):
            self.declarations[node.target.id] = annotated
        if node.value is None and dotted(node.target) in self.env:
            if not self.env[dotted(node.target)] and annotated in {
                "client",
                "unproven:client",
            }:
                self.bind(node.target, "unproven:client")
            return
        previous = self.binding
        self.binding = dotted(node.target)
        if node.value:
            self.visit(node.value)
            if (
                isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Name)
                and node.target.id == node.value.id
            ):
                self.binding = previous
                return
            escape_fields(node.value, self)
        self.visit(node.target)
        nonnull = (
            nonnull_client(node.value, self.resolver, self.env)
            if node.value is not None
            else required_client_annotation(
                node.annotation, self.resolver, self.annotation_env()
            )
        )
        self.bind(
            node.target,
            annotated_assignment(
                node,
                self.resolver.infer(node.value, self.env) if node.value else "",
                annotated,
            ),
        )
        if nonnull and (name := dotted(node.target)):
            self.env[name + ".@nonnull"] = "client"
        self.binding = previous

    def visit(self, node: ast.AST) -> None:
        scoped = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        outer_declarations = self.declarations
        if scoped:
            self.declarations = {}
        super().visit(node)
        if scoped:
            self.declarations = outer_declarations

    def visit_Assign(self, node: ast.Assign) -> None:
        previous = self.binding
        self.binding = dotted(node.targets[0])
        self.visit(node.value)
        if not all(
            isinstance(target, ast.Name)
            and isinstance(node.value, ast.Name)
            and target.id == node.value.id
            for target in node.targets
        ):
            escape_fields(node.value, self)
        for assignment, kind in assignment_proofs(
            node, lambda value: self.resolver.infer(value, self.env)
        ):
            target = assignment.targets[0]
            self.visit(target)
            if (
                isinstance(target, ast.Name)
                and isinstance(assignment.value, ast.Name)
                and target.id == assignment.value.id
            ):
                continue
            if (
                not self.scope
                and not self.in_class_body
                and isinstance(target, ast.Name)
                and (
                    kind in {"", "non-client"}
                    or kind.startswith(("type:", "annotation:", "alias:"))
                )
                and (
                    isinstance(node.value, (ast.Subscript, ast.BinOp))
                    or isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                )
            ):
                kind = annotation_alias(
                    node.value,
                    AnnotationProofs(self.resolver, {**self.env, **self.type_bindings}),
                )
            if isinstance(target, ast.Name) and target.id in self.declarations:
                kind = annotated_assignment(
                    assignment, kind, self.declarations[target.id]
                )
            if (
                isinstance(target, ast.Subscript)
                and CONTAINER_WRITE in self.env
                and indirect_writes(target)
                and not AttributeProofs(self.resolver, self.env).trusted(target)
            ):
                # The untrusted store's own value stays review-only evidence.
                kind = self.resolver.infer(
                    assignment.value,
                    {k: v for k, v in self.env.items() if k != CONTAINER_WRITE},
                )
            AttributeProofs(self.resolver, self.env, self.reflective_aliases).assign(
                assignment, kind
            )
        self.binding = previous

    def visit_TypeAlias(self, node: ast.AST) -> None:
        # This node class does not exist on the tool's Python 3.9 floor.
        if sys.version_info >= (3, 12) and isinstance(node, ast.TypeAlias):
            self.visit(node.value)
            if not self.scope and not self.in_class_body:
                self.bind(node.name, "alias:" + ast.unparse(node.value))

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        prefixes = self.env.copy()
        self.prefix_proofs = (*self.prefix_proofs, prefixes)
        transparent = True
        for item in node.items:
            self.visit(item.context_expr)
            expression = item.context_expr
            transparent = transparent and (
                self.resolver.infer(expression, self.env) == "context:non-suppressing"
                or self.resolver.infer(expression, self.env).startswith(
                    "context-value:"
                )
                or isinstance(expression, ast.Call)
                and self.resolver.infer(expression.func, self.env) == "type:client"
            )
            if item.optional_vars:
                self.visit(item.optional_vars)
                self.bind(
                    item.optional_vars,
                    context_entry(
                        self.resolver.infer(item.context_expr, self.env),
                        isinstance(node, ast.AsyncWith),
                    ),
                )
        reaches = self.visit_suite(node.body)
        if not transparent:
            # Suppression can reach the join with any visited body prefix.
            self.env = joined_bindings(self.resolver, (prefixes, self.env))
        self.reaching[node] = reaches or not transparent
        self.prefix_proofs = self.prefix_proofs[:-1]

    visit_AsyncWith = visit_With


def assignment_proofs(
    node: ast.Assign, infer: Callable[[ast.AST], str]
) -> list[tuple[ast.Assign, str]]:
    """Pair literal positions exactly; delegate inferred sequence shapes to binding."""

    def pairs(target: ast.expr, value: ast.expr) -> list[tuple[ast.Assign, str]]:
        if isinstance(target, (ast.Tuple, ast.List)):
            if (
                isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
                and not any(
                    isinstance(element, ast.Starred)
                    for element in [*target.elts, *value.elts]
                )
                and not any(
                    isinstance(child, ast.NamedExpr) for child in ast.walk(value)
                )
            ):
                return [
                    pair
                    for left, right in zip(target.elts, value.elts)
                    for pair in pairs(left, right)
                ]
            kind = (
                infer(value)
                if isinstance(value, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))
                else ""
            )
            if isinstance(value, ast.Call) and not kind.startswith("tuple:"):
                kind = ""
            return [(ast.Assign(targets=[target], value=value), kind)]
        return [(ast.Assign(targets=[target], value=value), infer(value))]

    return [pair for target in node.targets for pair in pairs(target, node.value)]
