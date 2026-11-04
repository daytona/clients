# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Preserve URL-bearing config data shared beyond client construction."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ._scopes import ScopeWrites

if TYPE_CHECKING:
    from .config_origins import ConfigOrigins


def shared_url_configs(origins: ConfigOrigins) -> dict[tuple[int, int], str]:
    """Index non-client readers by lexical binding once, independently of safety."""
    readers = {
        (origins.uses.owner(node), node.id)
        for node in origins.nodes.values()
        if isinstance(node.ctx, ast.Load) and not origins.uses.client_argument(node)
    }
    readers.update(
        (origins.module, name)
        for module, name in origins.flow.context.index.config_readers
        if module == origins.flow.context.module
    )
    shared: dict[tuple[int, int], str] = {}
    for node in origins.parents:
        # isinstance dispatch is necessary on the supported Python 3.9 runtime.
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(
            node.value, ast.Call
        ):
            continue
        if origins.kind(node.value.func) != "type:config":
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Name)
                and (origins.uses.owner(target), target.id) in readers
            ):
                shared[(node.value.lineno, node.value.col_offset)] = target.id
                break
    return shared


def shared_write(origins: ConfigOrigins, position: tuple[int, int]) -> bool:
    """Only an exclusively local constructor grants destructive write ownership."""
    node = origins.nodes.get(position)
    if node is None:
        return True
    if not origins.resolved_name(node):
        return True
    return other_readers(origins, position)


def other_readers(origins: ConfigOrigins, position: tuple[int, int]) -> bool:
    """Find independent lexical or indexed readers even without origin proof."""
    node = origins.nodes.get(position)
    if node is None:
        return False
    owner = origins.uses.owner(node)
    return any(
        candidate.id == node.id
        and origins.uses.owner(candidate) is owner
        and isinstance(candidate.ctx, ast.Load)
        and not origins.uses.client_argument(candidate)
        and not _write_target(origins, candidate)
        for candidate in origins.nodes.values()
    ) or (
        owner is origins.module
        and (origins.flow.context.module, node.id)
        in origins.flow.context.index.config_readers
    )


def _write_target(origins: ConfigOrigins, node: ast.Name) -> bool:
    """The URL mutation itself is not an independent observer of its value."""
    parent = origins.parents[node]
    return (
        isinstance(parent, ast.Attribute)
        and isinstance(parent.ctx, ast.Store)
        and parent.attr in {"api_url", "server_url"}
    ) or (
        isinstance(parent, ast.Call)
        and origins.kind(parent.func) == "call:builtins.setattr"
        and len(parent.args) == 3
        and parent.args[0] is node
        and isinstance(parent.args[1], ast.Constant)
        and parent.args[1].value in {"api_url", "server_url"}
    )


def _binding(origins: ConfigOrigins, node: ast.Name) -> ast.AST | None:
    """Follow unconditional assignments at each use; uncertain control flow is unknown."""
    current = node
    visited: set[ast.Name] = set()
    while current not in visited:
        visited.add(current)
        owner = origins.uses.owner(current)
        if owner is None or origins.scope(current) is not owner:
            return None
        writes = ScopeWrites()
        writes.generic_visit(owner)
        latest_event = max(
            (
                position
                for name, position in writes.events
                if name == current.id
                and position < (current.lineno, current.col_offset)
            ),
            default=(0, 0),
        )
        stores = [
            candidate
            for candidate in origins.nodes.values()
            if candidate.id == current.id
            and isinstance(candidate.ctx, (ast.Store, ast.Del))
            and origins.uses.owner(candidate) is owner
            and isinstance(
                assignment := origins.parents[candidate], (ast.stmt, ast.expr)
            )
            and (
                assignment.end_lineno or candidate.lineno,
                assignment.end_col_offset or candidate.col_offset,
            )
            < (current.lineno, current.col_offset)
        ]
        if not stores:
            if latest_event != (0, 0):
                return None
            return next(
                (
                    arg
                    for arg in origins.scopes[owner]
                    if isinstance(arg, ast.arg) and arg.arg == current.id
                ),
                None,
            )
        latest = max(
            stores, key=lambda candidate: (candidate.lineno, candidate.col_offset)
        )
        assignment = origins.parents[latest]
        if (
            not isinstance(assignment, (ast.Assign, ast.AnnAssign))
            or origins.parents[assignment] is not owner
            or latest_event
            > (
                assignment.end_lineno or assignment.lineno,
                assignment.end_col_offset or assignment.col_offset,
            )
        ):
            return None
        # AST dispatch preserves the supported Python 3.9 runtime.
        if isinstance(assignment.value, ast.Name):
            current = assignment.value
            continue
        return assignment
    return None


def visible_url_write(origins: ConfigOrigins, position: tuple[int, int]) -> bool:
    """Find URL writes to the object currently passed to this client constructor."""
    node = origins.nodes.get(position)
    if node is None:
        return False
    binding = _binding(origins, node)
    if binding is None:
        return False
    return any(
        _write_target(origins, candidate) and _binding(origins, candidate) is binding
        for candidate in origins.nodes.values()
    ) or any(
        isinstance(assignment, (ast.Assign, ast.AnnAssign))
        and assignment is binding
        and isinstance(assignment.value, ast.Call)
        and origins.kind(assignment.value.func) == "type:config"
        and any(
            keyword.arg in {"api_url", "server_url"}
            for keyword in assignment.value.keywords
        )
        for assignment in origins.parents
    )
