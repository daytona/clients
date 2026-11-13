# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Suggestion-only sandbox identity facts, joined by the existing flow analysis."""

from __future__ import annotations

import ast
from dataclasses import replace
from typing import Final

from .flow import Call, Flow
from .resolution import dotted

_OWNER: Final = ".<suggestion-owner>"
_IDENTITY: Final = ".<suggestion-identity>"


class ServiceOwners(Flow):
    """Accumulate ownership without changing receiver kinds or upgrade proofs.

    Private descendant keys participate in normal invalidation and branch joins.
    Identity tokens also fence owners whose sandbox variable was rebound while a
    saved service handle remained valid. The impossible identifier suffixes
    cannot collide with source bindings. AST dispatch uses Python 3.9 syntax.
    """

    def __init__(self, flow: Flow) -> None:
        super().__init__(flow.resolver, flow.context)
        self.owners: dict[str, tuple[str, str]] = {}
        self.sandboxes: dict[tuple[int, int], str] = {}
        self.clients: dict[tuple[int, int], str] = {}
        self.arguments: dict[tuple[int, int, int | None, int | None], str] = {}
        self.analyze(flow.source, flow.module)

    def owner(self, node: ast.AST) -> tuple[str, str] | None:
        """Resolve an expression's current sandbox identity, not just its type."""
        if isinstance(node, (ast.Await, ast.NamedExpr)):
            return self.owner(node.value)
        name = dotted(node)
        kind = self.resolver.infer(node, self.env)
        saved = self.owners.get(self.env.get(name + _OWNER, ""))
        if saved:
            sandbox, token = saved
            if self.env.get(sandbox + _IDENTITY) == token:
                return saved
        if kind in {"sandbox", "client"} and name:
            token = self.env.setdefault(name + _IDENTITY, str(id(node)))
            return name, token
        if (
            kind == "client.v0"
            and isinstance(node, ast.Attribute)
            and node.attr == "v0"
        ):
            return self.owner(node.value)
        definition = self.resolver.mapping.receivers.get(kind)
        # A handle a sandbox call produced (a PTY from
        # `sandbox.process.create_pty_session(...)`) is owned by that call's
        # sandbox; a new sandbox (a fork) is not.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and self.sandbox_produced(kind)
        ):
            return self.owner(node.func.value)
        if (
            isinstance(node, ast.Attribute)
            and definition
            and definition.parent
            and node.attr == definition.attr
            and self.resolver.infer(node.value, self.env).removesuffix(".v0")
            == definition.parent
        ):
            return self.owner(node.value)
        return None

    def sandbox_produced(self, kind: str) -> bool:
        definition = self.resolver.mapping.receivers.get(kind)
        return (
            kind != "sandbox"
            and definition is not None
            and any(
                producer.startswith("sandbox") for producer in definition.produced_by
            )
        )

    def record(self, target: ast.AST, owner: tuple[str, str] | None) -> None:
        """Attach a value's provenance only while the owner identity still holds."""
        name = dotted(target)
        if name and owner and self.env.get(owner[0] + _IDENTITY) == owner[1]:
            token = str(id(target))
            self.owners[token] = owner
            self.env[name + _OWNER] = token

    def visit_Assign(self, node: ast.Assign) -> None:
        owner = self.owner(node.value)
        super().visit_Assign(node)
        for target in node.targets:
            self.record(target, owner)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        owner = self.owner(node.value) if node.value else None
        super().visit_AnnAssign(node)
        self.record(node.target, owner)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        owner = self.owner(node.value)
        super().visit_NamedExpr(node)
        self.record(node.target, owner)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            owner = self.owner(node.func.value)
            if owner:
                destinations = (
                    self.clients
                    if self.resolver.infer(node.func.value, self.env).startswith(
                        "client"
                    )
                    else self.sandboxes
                )
                destinations[node.lineno, node.col_offset] = owner[0]
        super().visit_Call(node)

    def visit(self, node: ast.AST) -> None:
        super().visit(node)
        if (
            isinstance(node, ast.expr)
            and self.resolver.infer(node, self.env) == "sandbox"
        ):
            owner = self.owner(node)
            if owner:
                self.arguments[
                    node.lineno, node.col_offset, node.end_lineno, node.end_col_offset
                ] = owner[0]

    def argument_owner(self, node: ast.expr) -> str:
        """Match the entire argument, not a sandbox prefix such as box in box.id."""
        return self.arguments.get(
            (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset), ""
        )

    def explicit_client(self, call: Call) -> Call:
        """Retain explicit root expressions for guidance only, never upgrade proofs."""
        if not isinstance(call.node.func, ast.Attribute):
            return call
        receiver = call.node.func.value
        attributes: list[str] = []
        while isinstance(receiver, ast.Attribute) and receiver.attr != "v0":
            attributes.insert(0, receiver.attr)
            receiver = receiver.value
        if not isinstance(receiver, ast.Attribute) or receiver.attr != "v0":
            return call
        # `x.v0.create()` is only Daytona guidance in a file that imports the SDK.
        if not self.found:
            return call
        kind = "client"
        for attribute in attributes:
            kind = next(
                (
                    name
                    for name, definition in self.resolver.mapping.receivers.items()
                    if definition.parent == kind and definition.attr == attribute
                ),
                "",
            )
        if not kind:
            return call
        root = self.text.segment(receiver.value) or ""
        if not isinstance(
            receiver.value, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)
        ):
            root = "(" + root + ")"
        self.clients[call.node.lineno, call.node.col_offset] = root
        return replace(call, kind=kind + ".v0")

    def render(self, template: str, call: Call) -> str:
        """Use a known sandbox, or express the replacement on the service itself."""
        produced = self.sandbox_produced(call.kind)
        if not call.kind.startswith("sandbox.") and not produced:
            return template
        sandbox = self.sandboxes.get((call.node.lineno, call.node.col_offset))
        if sandbox:
            return template.replace("{sandbox}", sandbox)
        if produced:
            return template
        attributes: list[str] = []
        kind = call.kind
        while kind.startswith("sandbox."):
            definition = self.resolver.mapping.receivers[kind]
            if definition.attr:
                attributes.insert(0, definition.attr)
            kind = definition.parent or ""
        prefix = "{sandbox}." + ".".join(attributes) + "."
        return template.replace(prefix, call.receiver + ".")
