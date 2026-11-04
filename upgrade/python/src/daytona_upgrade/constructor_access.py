# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Relate eager namespace accesses to individual constructor evaluations."""

from __future__ import annotations

import ast

from ._scopes import ScopeWrites
from ._workflow_scopes import WorkflowScopes
from .flow import Flow
from .resolution import dotted


class ConstructorAccess:
    """Index bindings once; never equate distinct clients just by their proof kind.

    AST isinstance dispatch keeps the migration tool runnable on Python 3.9.
    """

    def __init__(self, flow: Flow) -> None:
        self.flow = flow
        self.scopes = WorkflowScopes(flow.module)
        self.bindings: list[
            tuple[ast.expr, ast.expr, ast.Assign | ast.AnnAssign | ast.NamedExpr]
        ] = []
        for node in self.scopes.nodes:
            if isinstance(node, ast.Assign):
                self.bindings.extend(
                    (target, node.value, node) for target in node.targets
                )
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value:
                self.bindings.append((node.target, node.value, node))

    def accessed(self, construction: ast.Call, boundary: ast.AST) -> bool:
        """Find an explicit eager .v0 read in the rewritten exception boundary."""
        writes = ScopeWrites(aliases=self.flow.reflective_aliases)
        writes.visit(boundary)

        def origin(expression: ast.expr) -> ast.expr | None:
            if isinstance(expression, ast.NamedExpr):
                return origin(expression.value)
            name = dotted(expression)
            if not name:
                return expression
            before = (expression.lineno, expression.col_offset)
            candidates = [
                (value, assignment)
                for target, value, assignment in self.bindings
                if dotted(target) == name
                and self.scopes.owner(name.split(".")[0], target)
                is self.scopes.owner(name.split(".")[0], expression)
                and (assignment.end_lineno or 0, assignment.end_col_offset or 0)
                <= before
            ]
            if not candidates:
                return None
            value, assignment = max(
                candidates,
                key=lambda pair: (pair[1].end_lineno or 0, pair[1].end_col_offset or 0),
            )
            if not self.eager(assignment, boundary):
                return None
            start = (assignment.end_lineno or 0, assignment.end_col_offset or 0)
            if any(
                start < position < before
                and (
                    changed == "*" or name == changed or name.startswith(changed + ".")
                )
                for changed, position in writes.events
            ):
                return None
            return origin(value)

        for node in ast.walk(boundary):
            if not isinstance(node, ast.Attribute) or not isinstance(
                node.ctx, ast.Load
            ):
                continue
            if not self.eager(node, boundary):
                continue
            if node.attr == "v0" and origin(node.value) is construction:
                return True
        return False

    def eager(self, node: ast.AST, boundary: ast.AST) -> bool:
        """Accept only unconditional evaluation in the try body's straight-line path.

        Eager comprehension elements are included, but generator elements and
        comprehension filters/inner iterables cannot establish a first access.
        AST isinstance dispatch preserves the supported Python 3.9 runtime.
        """
        child = node
        parent = self.scopes.parents.get(child)
        while parent is not None and parent is not boundary:
            if isinstance(parent, ast.stmt) and not isinstance(
                parent,
                (
                    ast.Assign,
                    ast.AnnAssign,
                    ast.AugAssign,
                    ast.Expr,
                    ast.Return,
                    ast.Raise,
                ),
            ):
                return False
            if isinstance(parent, ast.Lambda):
                return False
            if isinstance(parent, ast.BoolOp) and child is not parent.values[0]:
                return False
            if isinstance(parent, ast.IfExp) and child is not parent.test:
                return False
            if isinstance(parent, ast.comprehension):
                comprehension = self.scopes.parents[parent]
                if (
                    not isinstance(
                        comprehension,
                        (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
                    )
                    or child is not parent.iter
                    or parent is not comprehension.generators[0]
                ):
                    return False
            if (
                isinstance(parent, ast.GeneratorExp)
                and child is not parent.generators[0]
            ):
                return False
            child, parent = parent, self.scopes.parents.get(parent)
        body = getattr(boundary, "body", ())
        if parent is not boundary or child not in body:
            return False
        # A prior transfer (including one in a branch) can bypass the access.
        return not any(
            isinstance(nested, (ast.Return, ast.Raise, ast.Break, ast.Continue))
            for statement in body[: body.index(child)]
            for nested in ast.walk(statement)
        )
