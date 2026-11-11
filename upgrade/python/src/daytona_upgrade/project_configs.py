# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fence module config origins when project imports expose mutable objects."""

from __future__ import annotations

import ast
from dataclasses import replace
from typing import TYPE_CHECKING

from .config_origins import ConfigOrigins
from .resolution import ProjectIndex, dotted, relative_module
from .source_suffixes import PACKAGE_INITIALIZERS

if TYPE_CHECKING:
    from .flow import Flow


class ProjectConfigs:
    """Resolve import paths to config owners, including wildcard re-exports."""

    def __init__(self, flows: dict[str, Flow]) -> None:
        self.origins = {module: ConfigOrigins(flow) for module, flow in flows.items()}
        self.configs: set[tuple[str, str]] = set()
        self.imports: dict[tuple[str, ast.AST, str], set[tuple[str, str]]] = {}
        self.stars: dict[tuple[str, ast.AST], set[str]] = {}
        for module, origins in self.origins.items():
            for node in ast.walk(origins.module):
                # The supported Python 3.9 runtime requires AST isinstance dispatch.
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    if (
                        origins.scope(node) is origins.module
                        and isinstance(node.value, ast.Call)
                        and origins.kind(node.value.func) == "type:config"
                    ):
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        self.configs.update(
                            (module, target.id)
                            for target in targets
                            if isinstance(target, ast.Name)
                        )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        target = (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
                        self.imports.setdefault(
                            (module, origins.scope(node), local), set()
                        ).add((target, ""))
                if isinstance(node, ast.ImportFrom):
                    current = module
                    if origins.flow.context.file.endswith(
                        tuple("/" + name for name in PACKAGE_INITIALIZERS)
                    ):
                        current += ".__init__"
                    target = relative_module(current, node)
                    for alias in node.names:
                        if alias.name == "*":
                            self.stars.setdefault(
                                (module, origins.scope(node)), set()
                            ).add(target)
                        else:
                            self.imports.setdefault(
                                (
                                    module,
                                    origins.scope(node),
                                    alias.asname or alias.name,
                                ),
                                set(),
                            ).add((target, alias.name))

    def references(self, module: str, node: ast.expr) -> set[tuple[str, str]]:
        """Follow only imports owned by this lexical reference, not shadow names."""
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name):
            return set()
        origins = self.origins[module]
        scope = origins.uses.owner(root) or origins.module
        pending = [(module, dotted(node), scope)]
        seen: set[tuple[str, str, ast.AST]] = set()
        found: set[tuple[str, str]] = set()
        while pending:
            current, name, scope = pending.pop()
            if (current, name, scope) in seen:
                continue
            seen.add((current, name, scope))
            owner = self.origins.get(current)
            if owner is None:
                for offset, character in enumerate(name):
                    target = current + "." + name[:offset]
                    destination = self.origins.get(target)
                    if character == "." and destination is not None:
                        pending.append((target, name[offset + 1 :], destination.module))
                continue
            if scope is owner.module and (current, name) in self.configs:
                found.add((current, name))
            base, _, tail = name.partition(".")
            destinations: list[tuple[str, str]] = []
            for target, member in self.imports.get((current, scope, base), set()):
                destinations.append(
                    (target, ".".join(part for part in (member, tail) if part))
                )
            destinations.extend(
                (target, name) for target in self.stars.get((current, scope), set())
            )
            if scope is owner.module:
                destinations.extend(
                    (current + "." + name[:offset], name[offset + 1 :])
                    for offset, character in enumerate(name)
                    if character == "."
                )
            for target, member in destinations:
                destination = self.origins.get(target)
                if destination is not None:
                    pending.append((target, member, destination.module))
                else:
                    pending.append((target, member, scope))
        return found

    def escapes(self) -> frozenset[tuple[str, str]]:
        escaped: set[tuple[str, str]] = set()
        for module, origins in self.origins.items():
            for node in origins.parents:
                if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
                    node.ctx, ast.Load
                ):
                    continue
                parent = origins.parents[node]
                # Foreign URL writes are not rewritten by the declaring module.
                write = isinstance(parent, ast.Attribute) and not isinstance(
                    parent.ctx, ast.Load
                )
                setter = (
                    isinstance(parent, ast.Call)
                    and origins.kind(parent.func) == "call:builtins.setattr"
                )
                if not write and not setter and origins.uses.safe(node):
                    continue
                escaped.update(
                    origin
                    for origin in self.references(module, node)
                    if origin[0] != module
                )
        return frozenset(escaped)

    def readers(self) -> frozenset[tuple[str, str]]:
        """Track external observations separately from mutation/URL-safety fences."""
        return frozenset(
            origin
            for module, origins in self.origins.items()
            for node in origins.parents
            if isinstance(node, (ast.Name, ast.Attribute))
            and isinstance(node.ctx, ast.Load)
            and not origins.uses.client_argument(node)
            for origin in self.references(module, node)
            if origin[0] != module
        )


def inspect_configs(index: ProjectIndex, flows: dict[str, Flow]) -> ProjectIndex:
    """Finish the shared index only after import inference has reached a fixed point."""
    project = ProjectConfigs(flows)
    builtin_writes = frozenset(
        node.attr
        for origin in project.origins.values()
        for node in origin.parents
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        and origin.kind(node.value) == "module:builtins"
    )
    index = replace(index, builtin_writes=builtin_writes)
    for flow in flows.values():
        flow.context = replace(flow.context, index=index)
    hooks = any(origin.uses.hooks for origin in project.origins.values())
    for origins in project.origins.values():
        origins.uses.hooks = hooks
    return replace(
        index,
        config_escapes=project.escapes(),
        config_hooks=hooks,
        config_readers=project.readers(),
    )
