# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bound factory patch reviews to calls in their installation's lifetime."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .double_ownership import scope_nodes
from .parameter_relay_uses import RelayUses
from .pytest_collection import matches_name

if TYPE_CHECKING:
    from .client_double_targets import ClientDoubleTargets
    from .flow import Flow


def namespace_calls(
    flows: dict[str, Flow],
) -> dict[tuple[str, int, int], frozenset[str]]:
    """Link calls to factories consumed through v0, including indexed relays."""
    edges: dict[str, set[str]] = {}
    consumers: dict[str, set[str]] = {}
    calls: dict[tuple[str, int, int], tuple[str, bool]] = {}
    for module, flow in flows.items():
        parents = {
            child: parent
            for parent in ast.walk(flow.module)
            for child in ast.iter_child_nodes(parent)
        }
        scopes: dict[ast.AST, dict[ast.AST, ast.AST]] = {}
        for call, called in flow.factory_calls.items():
            if not isinstance(call, ast.Call):
                continue
            scope: ast.AST = call
            while scope in parents and not isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)
            ):
                scope = parents[scope]
            owner = (
                f"fn:{module}:{scope.lineno}"
                if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                else f"module:{module}"
            )
            if scope not in scopes:
                scopes[scope] = {
                    child: parent
                    for parent in ast.walk(scope)
                    for child in ast.iter_child_nodes(parent)
                }
            changed = (
                RelayUses(flow, scopes[scope], "client")
                .inspect(call, "return")
                .namespace
            )
            calls[module, call.lineno, call.col_offset] = called, changed
            edges.setdefault(owner, set()).add(called)
            if changed:
                consumers.setdefault(owner, set()).add(called)
    reachable = {owner: children.copy() for owner, children in edges.items()}
    while True:
        additions = {
            owner: set().union(*(reachable.get(child, set()) for child in children))
            - children
            for owner, children in reachable.items()
        }
        if not any(additions.values()):
            break
        for owner, children in additions.items():
            reachable[owner].update(children)
    for owner, factories in consumers.items():
        consumers[owner] = factories | set().union(
            *(reachable.get(factory, set()) for factory in factories)
        )
    while True:
        additions = {
            owner: set().union(*(consumers.get(child, set()) for child in children))
            - consumers.get(owner, set())
            for owner, children in edges.items()
        }
        if not any(additions.values()):
            break
        for owner, factories in additions.items():
            consumers.setdefault(owner, set()).update(factories)
    return {
        position: frozenset(
            consumers.get(called, set())
            | ({called} | reachable.get(called, set()) if changed else set())
        )
        for position, (called, changed) in calls.items()
    }


def patch_reaches_factory(
    node: ast.Call, contract: str, targets: ClientDoubleTargets
) -> bool:
    """Unknown lifetimes remain reviewable; known scopes require a call path."""
    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
        keyword.arg is None for keyword in node.keywords
    ):
        return True
    parents = {
        child: parent
        for parent in ast.walk(targets.flow.module)
        for child in ast.iter_child_nodes(parent)
    }
    child: ast.AST = node
    body: list[ast.stmt] | None = None
    while child in parents:
        parent = parents[child]
        if isinstance(parent, (ast.With, ast.AsyncWith)) and child in parent.items:
            body = parent.body
            break
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child in parent.decorator_list or matches_name(
                parent.name, targets.flow.context.index.collection.functions
            ):
                body = parent.body
            break
        child = parent
    if body is None:
        return True
    return any(
        contract
        in targets.flow.context.index.namespace_calls.get(
            (targets.flow.context.module, call.lineno, call.col_offset), ()
        )
        for statement in body
        for call in scope_nodes(statement)
        if isinstance(call, ast.Call)
    )
