# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Local config provenance; receiver types alone cannot prove URL safety."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .config_members import factory, resolved_factory
from .config_readers import shared_url_configs
from .config_uses import ConfigUses
from .subclasses import client_constructor

if TYPE_CHECKING:
    from .flow import Flow


class ConfigOrigins:
    """Index lexical ownership once, then fence aliases, escapes and mutations."""

    def __init__(self, flow: Flow) -> None:
        self.flow = flow
        self.module = ast.parse(flow.source)
        self.parents = {
            child: parent
            for parent in ast.walk(self.module)
            for child in ast.iter_child_nodes(parent)
        }
        self.nodes = {
            (node.lineno, node.col_offset): node
            for node in ast.walk(self.module)
            if isinstance(node, ast.Name)
        }
        self.calls: dict[tuple[int, int], ast.Call] = {}
        for node in ast.walk(self.module):
            if isinstance(node, ast.Call):
                self.calls.setdefault((node.lineno, node.col_offset), node)
        self.scopes: dict[ast.AST, list[ast.AST]] = {}
        for node in ast.walk(self.module):
            self.scopes.setdefault(self.scope(node), []).append(node)
        self.uses = ConfigUses(self)
        self.preserved: set[tuple[int, int]] = set()
        # Nested calls can share a start position; the selected-call lookup above
        # must not hide any client constructor from the preservation scan.
        for call in (node for node in self.parents if isinstance(node, ast.Call)):
            if not client_constructor(self.kind(call.func)):
                continue
            values = [*call.args, *(keyword.value for keyword in call.keywords)]
            for value in values:
                if not isinstance(value, ast.Name) or self.resolved(
                    (value.lineno, value.col_offset)
                ):
                    continue
                owner = self.uses.owner(value)
                for candidate in self.scopes[owner] if owner is not None else []:
                    if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                        continue
                    targets = (
                        candidate.targets
                        if isinstance(candidate, ast.Assign)
                        else [candidate.target]
                    )
                    if any(
                        isinstance(target, ast.Name) and target.id == value.id
                        for target in targets
                    ):
                        if (
                            isinstance(candidate.value, ast.Call)
                            and self.kind(candidate.value.func) == "type:config"
                        ):
                            self.preserved.add(
                                (candidate.value.lineno, candidate.value.col_offset)
                            )
        self.shared_urls = shared_url_configs(self)

    def scope(self, node: ast.AST) -> ast.AST:
        child = node
        parent = self.parents.get(node)
        while parent is not None:
            # Defaults/decorators/annotations execute outside the function body.
            if isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ) and not isinstance(node, (ast.arg, ast.arguments)):
                body = parent.body
                if child is not body and not (isinstance(body, list) and child in body):
                    child = parent
                    parent = self.parents.get(parent)
                    continue
            if isinstance(
                parent,
                (
                    ast.Module,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                return parent
            child = parent
            parent = self.parents.get(parent)
        return self.module

    def kind(self, node: ast.expr) -> str:
        return self.flow.kinds.get(
            (
                node.lineno,
                node.col_offset,
                node.end_lineno or node.lineno,
                node.end_col_offset or node.col_offset,
            ),
            "",
        )

    def proven(self, position: tuple[int, int]) -> bool:
        node = self.nodes.get(position)
        if not isinstance(node, ast.Name):
            return False
        owner = self.uses.owner(node)
        if self.kind(node) != "config":
            # Shared declarations fence inherited flow kinds even for a mere
            # attribute write. A single unconditional lexical constructor still
            # establishes that write's target without trusting an annotation.
            stores = [
                child
                for child in self.nodes.values()
                if child.id == node.id
                and isinstance(child.ctx, (ast.Store, ast.Del))
                and self.uses.owner(child) is owner
            ]
            if owner is None or len(stores) != 1:
                return False
            origin = self.parents[stores[0]]
            if not (
                isinstance(origin, (ast.Assign, ast.AnnAssign))
                and self.parents[origin] is owner
                and isinstance(origin.value, ast.Call)
                and self.kind(origin.value.func) == "type:config"
                and (origin.end_lineno or origin.lineno) < node.lineno
                and node.id in self.uses.shared[self.scope(node)]
            ):
                return False
        for candidate in self.scopes[owner] if owner is not None else []:
            if isinstance(candidate, (ast.Assign, ast.AnnAssign)):
                targets = (
                    candidate.targets
                    if isinstance(candidate, ast.Assign)
                    else [candidate.target]
                )
                if any(
                    isinstance(target, ast.Name) and target.id == node.id
                    for target in targets
                ):
                    if isinstance(candidate.value, ast.Call) and (
                        self.kind(candidate.value.func)
                        in {"type:config", "fn:literal-config"}
                        or factory(candidate.value, self)
                    ):
                        return True
                    if (
                        candidate.lineno,
                        candidate.col_offset,
                    ) in self.flow.config_annotations:
                        return True
            if isinstance(candidate, ast.arg) and candidate.arg == node.id:
                if (
                    candidate.lineno,
                    candidate.col_offset,
                ) in self.flow.config_annotations:
                    return True
        return False

    def resolved(self, position: tuple[int, int]) -> bool:
        node = self.calls.get(position) or self.nodes.get(position)
        if isinstance(node, ast.Call):
            return self.resolved_call(node)
        return isinstance(node, ast.Name) and self.resolved_name(node)

    def resolved_call(self, node: ast.Call) -> bool:
        """Check the selected factory, not an inner call at the same start column."""
        return self.kind(node.func) in {
            "type:config",
            "fn:literal-config",
        } or resolved_factory(node, self)

    def resolved_name(self, node: ast.Name) -> bool:
        """Resolve the reference even when a containing call starts at its position."""
        position = (node.lineno, node.col_offset)
        if not self.proven(position):
            return False
        scope = self.uses.owner(node)
        if scope is None:
            return False
        if (
            scope is self.module
            and (
                self.flow.context.module,
                node.id,
            )
            in self.flow.context.index.config_escapes
        ):
            return False
        stores = [
            child
            for child in self.scopes[scope]
            if isinstance(child, ast.Name)
            and child.id == node.id
            and isinstance(child.ctx, (ast.Store, ast.Del))
        ]
        if len(stores) != 1:
            return False
        origin = self.parents[stores[0]]
        if not isinstance(origin, (ast.Assign, ast.AnnAssign)):
            return False
        # proven() requires the flow proof at this use, including inside branches.
        if not isinstance(origin.value, ast.Call):
            return False
        if not self.resolved_call(origin.value):
            return False
        if isinstance(origin, ast.Assign) and len(origin.targets) != 1:
            return False
        if (origin.end_lineno or origin.lineno, origin.end_col_offset or 0) >= position:
            return False
        return all(
            self.uses.safe(child)
            for child in self.nodes.values()
            if child.id == node.id
            and isinstance(child.ctx, ast.Load)
            and self.uses.owner(child) is scope
        )
