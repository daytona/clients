# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Certify patched factory results against every indexed consumer, not annotations."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, NamedTuple

from .double_ownership import mutates_namespace
from .local_double_targets import DoubleTargets
from .parameter_relay_uses import RelayUses

if TYPE_CHECKING:
    from .flow import Flow


class FactoryConsumers(NamedTuple):
    adaptable: frozenset[str]
    namespace: frozenset[str]


def factory_consumers(flows: dict[str, Flow]) -> FactoryConsumers:
    """Require a changed access and no opaque uses for every result of a factory."""
    candidates: set[str] = set()
    unsafe: set[str] = set()
    namespace: set[str] = set()
    returned: set[tuple[str, str]] = set()
    for flow in flows.values():
        parents = {
            child: parent
            for parent in ast.walk(flow.module)
            for child in ast.iter_child_nodes(parent)
        }
        scopes: dict[ast.AST, dict[ast.AST, ast.AST]] = {}
        for call, contract in flow.factory_calls.items():
            if not isinstance(call, ast.Call):
                parent = parents.get(call)
                if DoubleTargets(flow).kind(call) == "fn:client" and not (
                    isinstance(parent, ast.Call) and parent.func is call
                ):
                    unsafe.add(contract)
                continue
            if DoubleTargets(flow).kind(call.func) != "fn:client":
                continue
            scope: ast.AST = call
            while scope in parents and not isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)
            ):
                scope = parents[scope]
            if scope not in scopes:
                scopes[scope] = {
                    child: parent
                    for parent in ast.walk(scope)
                    for child in ast.iter_child_nodes(parent)
                }
            use = RelayUses(flow, scopes[scope], "client").inspect(call, "return")
            if use.namespace:
                namespace.add(contract)
            if use.returned and isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                returned.add((contract, f"fn:{flow.context.module}:{scope.lineno}"))
            candidates.add(contract)
            if (
                not use.changed
                or not use.safe
                or use.returned
                or mutates_namespace(scope)
            ):
                unsafe.add(contract)
    while (
        additions := {inner for inner, outer in returned if outer in namespace}
        - namespace
    ):
        namespace.update(additions)
    return FactoryConsumers(frozenset(candidates - unsafe), frozenset(namespace))
