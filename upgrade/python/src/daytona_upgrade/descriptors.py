# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class-member lookup proofs preserve descriptor creation-time bindings."""

from __future__ import annotations

import ast
from typing import Protocol

from ._scopes import ScopeWrites
from .annotation_exports import joined_bindings
from .annotation_proofs import AnnotationProofs
from .call_sites import BOUND_CALL_PARAMETERS, CALL_PARAMETERS, call_parameters
from .class_stores import property_setter
from .client_parameters import BOUND_PARAMETERS, PARAMETERS, parameter_contract
from .generator_proofs import function_bindings
from .mock_spec_origins import SYNC_MARKER, MockSpecOrigins
from .proof_mutations import invalidate
from .resolution import Resolver, dotted


class DescriptorContext(Protocol):
    """Class analysis separates descriptor creation from deferred getter execution."""

    resolver: Resolver
    definition_envs: dict[ast.AST, dict[str, str]]
    deferred: dict[ast.AST, set[str]]


def class_members(
    node: ast.ClassDef, context: DescriptorContext, annotations: dict[str, str]
) -> dict[str, str]:
    """Seed descriptor lookup results and independent synchronous spec provenance."""
    bindings: dict[str, str] = {}
    for statement in node.body:
        writes = ScopeWrites()
        writes.visit(statement)
        for name, _ in writes.events:
            bindings.pop(node.name + "." + name + SYNC_MARKER, None)
            bindings.pop(node.name + "." + name + PARAMETERS, None)
            bindings.pop(node.name + "." + name + BOUND_PARAMETERS, None)
            bindings.pop(node.name + "." + name + CALL_PARAMETERS, None)
            bindings.pop(node.name + "." + name + BOUND_CALL_PARAMETERS, None)
            bindings.pop("self." + name + CALL_PARAMETERS, None)
        if not isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) or property_setter(statement):
            continue
        instance, lookup = member_results(statement, context, annotations)
        definition = context.definition_envs[statement]
        definition_annotations = {
            **{
                name: kind
                for name, kind in annotations.items()
                if name.split(".")[0] not in definition
            },
            **definition,
        }
        origins = MockSpecOrigins(context.resolver, definition_annotations)
        bindings["self." + statement.name] = instance
        bindings[node.name + "." + statement.name] = lookup
        decorators = {
            context.resolver.infer(value, annotations)
            for value in statement.decorator_list
        }
        for suffix, bound in (
            (PARAMETERS, "call:builtins.classmethod" in decorators),
            (BOUND_PARAMETERS, "call:builtins.staticmethod" not in decorators),
        ):
            bindings[node.name + "." + statement.name + suffix] = parameter_contract(
                statement,
                AnnotationProofs(context.resolver, definition_annotations),
                bound,
            )
        for suffix, bound in ((CALL_PARAMETERS, False), (BOUND_CALL_PARAMETERS, True)):
            bindings[node.name + "." + statement.name + suffix] = call_parameters(
                statement, definition_annotations.get("@module", ""), bound
            )
        bindings["self." + statement.name + CALL_PARAMETERS] = bindings[
            node.name + "." + statement.name + BOUND_CALL_PARAMETERS
        ]
        if "type:client" in lookup:
            bindings[node.name + "." + statement.name + ".@mock-linked"] = (
                "unproven:client"
            )
        if (
            isinstance(statement, ast.FunctionDef)
            and lookup == "fn:type:client"
            and origins.synchronous_annotation(statement.returns)
        ):
            bindings[node.name + "." + statement.name + SYNC_MARKER] = lookup
    return bindings


def member_results(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    context: DescriptorContext,
    annotations: dict[str, str],
) -> tuple[str, str]:
    """Return instance/class lookup proofs with distinct deferred yield bindings."""
    runtime = function_bindings(node, context.definition_envs[node], context.deferred)
    kind = context.resolver.function_kind(node, annotations, runtime)
    descriptor = property_result(node, context, annotations)
    if descriptor is None:
        method = "fn:" + kind if kind else ""
        return method, method
    # A newly inferred may-return cannot overrule an existing exact descriptor proof.
    if node.returns is None and descriptor == "client" and kind == "unproven:client":
        kind = descriptor
    binding = descriptor or kind
    if (
        descriptor
        and kind
        and descriptor != kind
        and {descriptor, kind}.intersection({"client", "unproven:client"})
    ):
        binding = "unproven:client"
    return binding, "property:" + binding


def property_result(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    context: DescriptorContext,
    annotations: dict[str, str],
) -> str | None:
    """Distinguish descriptors from methods; infer only a direct synchronous return."""
    resolver = context.resolver
    runtime = context.definition_envs[node]
    descriptors = {"call:builtins.property", "call:functools.cached_property"}
    decorators = [resolver.infer(value, runtime) for value in node.decorator_list]
    if not descriptors.intersection(decorators):
        return None
    if isinstance(node, ast.AsyncFunctionDef) or len(decorators) != 1:
        return (
            "unproven:client"
            if resolver.annotation(node.returns, annotations) == "client"
            else ""
        )
    local = runtime.copy()
    for name in context.deferred.get(node, set()):
        previous = local.copy()
        invalidate(local, name)
        local = joined_bindings(resolver, (previous, local))
    for parameter in [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg else []),
        *([node.args.kwarg] if node.args.kwarg else []),
    ]:
        invalidate(local, parameter.arg)
    if len(node.body) != 1:
        pending: list[ast.AST] = list(node.body)
        while pending:
            child = pending.pop()
            if isinstance(child, ast.Return) and child.value is not None:
                kind = resolver.infer(child.value, local)
                if kind in {"client", "unproven:client"} or (
                    not kind
                    and isinstance(child.value, ast.Call)
                    and "daytona" in dotted(child.value.func).lower()
                ):
                    return "unproven:client"
            elif not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                pending.extend(ast.iter_child_nodes(child))
        return ""
    # The tool runs on Python 3.9, where structural matching is unavailable.
    statement = node.body[0]
    if isinstance(statement, ast.Return) and statement.value is not None:
        kind = resolver.infer(statement.value, local)
        if (
            not kind
            and isinstance(statement.value, ast.Call)
            and "daytona" in dotted(statement.value.func).lower()
        ):
            return "unproven:client"
        return kind
    return ""
