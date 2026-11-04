# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class provenance independent of the shared synchronous/asynchronous client type."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .annotation_flow import annotation_type_nodes
from .expression_names import dotted
from .subclasses import client_constructor

if TYPE_CHECKING:
    from .flow import SourceContext
    from .resolution import Resolver

_MARKER: Final = ".@async-mock-spec"
SYNC_MARKER: Final = ".@sync-mock-spec"


@dataclass(frozen=True)
class MockSpecOrigins:
    """Require synchronous origins separately from the ordinary client proof identity."""

    resolver: Resolver
    env: dict[str, str]

    def imported(self, module: str, name: str) -> bool:
        """Resolve the original SDK import, including project re-exports."""
        if self.resolver.index.owns(module):
            return bool(self.resolver.index.exports.get(module, {}).get(name + _MARKER))
        return (
            name == "AsyncDaytona"
            and self.resolver.imported(module, name) == "type:client"
        )

    def asynchronous(self, node: ast.AST) -> bool:
        """Inspect the spec expression, not the mock helper's name or result type."""
        if isinstance(node, ast.NamedExpr):
            return self.asynchronous(node.value)
        if isinstance(node, ast.IfExp):
            branches = (node.body, node.orelse)
            if any(self.asynchronous(branch) for branch in branches):
                return True
            proven = tuple(
                client_constructor(self.resolver.infer(branch, self.env))
                for branch in branches
            )
            return any(proven) and not all(proven)
        expression = node.func if isinstance(node, ast.Call) else node
        if self.env.get(dotted(expression) + _MARKER):
            return True
        if dotted(expression) in self.env:
            return False
        if isinstance(expression, ast.Attribute):
            parent = self.resolver.infer(expression.value, self.env)
            if parent.startswith(("module:", "local-module:")):
                return self.imported(parent.split(":", 1)[1], expression.attr)
        return False

    def synchronous(self, node: ast.AST) -> bool:
        """Require positive class provenance, never absence of async evidence."""
        if isinstance(node, ast.NamedExpr):
            return self.synchronous(node.value)
        if isinstance(node, ast.IfExp):
            return all(self.synchronous(branch) for branch in (node.body, node.orelse))
        expression = node.func if isinstance(node, ast.Call) else node
        expected = "fn:type:client" if isinstance(node, ast.Call) else "type:client"
        return self.origin(expression) == expected

    def origin(self, expression: ast.AST, marker: str = SYNC_MARKER) -> str:
        """Read a class or factory marker through its current binding, not its spelling."""
        if marker == ".@mock-linked":
            if isinstance(expression, ast.NamedExpr):
                return self.origin(expression.value, marker)
            if isinstance(expression, ast.IfExp):
                return self.origin(expression.body, marker) or self.origin(
                    expression.orelse, marker
                )
            if isinstance(expression, ast.Call):
                return self.origin(expression.func, marker)
        name = dotted(expression)
        if name + marker in self.env:
            return self.env[name + marker]
        if name in self.env:
            return ""
        if isinstance(expression, ast.Attribute):
            parent = self.resolver.infer(expression.value, self.env)
            if parent.startswith(("instance:", "class:")):
                return self.env.get(
                    parent.split(":")[1] + "." + expression.attr + marker, ""
                )
            if parent.startswith(("module:", "local-module:")):
                module = parent.split(":", 1)[1]
                return (
                    self.resolver.index.exports.get(module, {}).get(
                        expression.attr + marker, ""
                    )
                    if self.resolver.index.owns(module)
                    else "type:client"
                    if marker == SYNC_MARKER
                    and expression.attr == "Daytona"
                    and self.resolver.imported(module, expression.attr) == "type:client"
                    else ""
                )
        return ""

    def synchronous_annotation(self, node: ast.AST | None) -> bool:
        """Certify type[Daytona], including quoted aliases, but not erased client types."""
        if node is None or self.resolver.annotation(node, self.env) != "type:client":
            return False
        nodes = tuple(self.type_nodes(node))
        if any(
            isinstance(child, ast.Constant)
            and child.value is None
            or self.resolver.infer(child, self.env)
            in {"module:typing.Optional", "module:typing_extensions.Optional"}
            for child in nodes
        ):
            return False
        classes = [
            child
            for child in nodes
            if self.resolver.infer(child, self.env) == "type:client"
        ]
        return bool(classes) and all(self.synchronous(child) for child in classes)

    def annotation(self, node: ast.AST) -> bool:
        """Retain async annotation origins without changing shared client proofs."""
        return any(self.asynchronous(child) for child in self.type_nodes(node))

    def type_nodes(self, node: ast.AST) -> Iterator[ast.AST]:
        """Expand type syntax and quoted aliases without inspecting Annotated extras."""
        pending = [node]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            for child in annotation_type_nodes(
                current, lambda value: self.resolver.infer(value, self.env)
            ):
                yield child
                value = self.resolver.infer(child, self.env)
                text = (
                    child.value
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    else value.removeprefix("alias:")
                    if value.startswith("alias:")
                    else ""
                )
                if text and text not in seen:
                    seen.add(text)
                    try:
                        pending.append(ast.parse(text, mode="eval").body)
                    except SyntaxError:
                        continue

    def bindings(self, node: ast.AST, context: SourceContext) -> dict[str, str]:
        """Capture origins before stores invalidate their destination metadata."""
        from .importflow import import_owner

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Function metadata is seeded with the other forward summaries.
            return {
                **(
                    {node.name + _MARKER: "fn:unproven:client"}
                    if node.returns is not None and self.annotation(node.returns)
                    else {}
                ),
                **(
                    {node.name + SYNC_MARKER: "fn:type:client"}
                    if isinstance(node, ast.FunctionDef)
                    and self.synchronous_annotation(node.returns)
                    else {}
                ),
            }
        names: list[str] = []
        synchronous: dict[str, str] = {}
        if isinstance(node, ast.ImportFrom):
            owner = import_owner(context, node)
            for alias in node.names:
                if alias.name == "*":
                    candidates = (
                        self.resolver.index.wildcard_names(owner)
                        if self.resolver.index.owns(owner)
                        else ("AsyncDaytona",)
                    )
                    names.extend(
                        name
                        for name in candidates
                        if name.isidentifier() and self.imported(owner, name)
                    )
                elif self.imported(owner, alias.name):
                    names.append(alias.asname or alias.name)
                candidates = (
                    self.resolver.index.wildcard_names(owner)
                    if alias.name == "*" and self.resolver.index.owns(owner)
                    else ("Daytona",)
                    if alias.name == "*"
                    else (alias.name,)
                )
                for name in candidates:
                    origin = (
                        self.resolver.index.exports.get(owner, {}).get(
                            name + SYNC_MARKER, ""
                        )
                        if self.resolver.index.owns(owner)
                        else "type:client"
                        if name == "Daytona"
                        and self.resolver.imported(owner, name) == "type:client"
                        else ""
                    )
                    if origin in {"type:client", "fn:type:client"}:
                        synchronous[alias.asname or name] = origin
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            and node.value is not None
        ):
            if self.asynchronous(node.value):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                names.extend(dotted(target) for target in targets if dotted(target))
            origin = (
                "type:client"
                if self.synchronous(node.value)
                else self.origin(node.value)
            )
            if origin in {"type:client", "fn:type:client"}:
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                synchronous.update(
                    (dotted(target), origin) for target in targets if dotted(target)
                )
        # A client-type marker participates in existing conservative branch joins;
        # it is metadata only, never the ordinary value's proof kind.
        return {
            **{name + _MARKER: "type:client" for name in names},
            **{name + SYNC_MARKER: kind for name, kind in synchronous.items()},
        }
