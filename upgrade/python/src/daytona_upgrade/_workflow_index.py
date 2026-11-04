# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Binding-indexed workflow calls and rebinding fences for one module."""

from __future__ import annotations

import ast
from bisect import bisect_right
from dataclasses import replace

from ._workflow_scopes import WorkflowScopes
from .flow import Call, Flow
from .resolution import dotted
from .suggestion_owners import ServiceOwners


class WorkflowIndex:
    """Accumulate suggestion-only captures without widening upgrade receiver proofs."""

    def __init__(self, flow: Flow, owners: ServiceOwners) -> None:
        self.flow = flow
        self.owners = owners
        self.scopes = WorkflowScopes(flow.module)
        self.calls: dict[tuple[ast.AST, str], list[Call]] = {}
        self.fences: dict[tuple[ast.AST, str], list[tuple[int, int]]] = {}
        self.aliases: dict[tuple[ast.AST, str, tuple[int, int]], ast.expr] = {}
        self.initial_stores: dict[ast.Call, set[tuple[int, int]]] = {}
        self.analyzed_calls = tuple(owners.explicit_client(call) for call in flow.calls)
        self.producers = tuple(
            call
            for call in self.analyzed_calls
            if call.binding
            and any(
                entry.starts_workflow
                and entry.receiver == call.kind.removesuffix(".v0")
                and entry.method == call.method
                for entry in flow.resolver.mapping.suggestions
            )
        )
        bindings = {call.binding for call in self.producers}
        for scope, writes in self.scopes.writes.items():
            for name, position in writes.events:
                owner = (
                    self.scopes.binding_owner(name.split(".")[0], scope)
                    if name.split(".")[0] in writes.shared
                    else scope
                )
                self.fences.setdefault((owner, name), []).append(position)
        for positions in self.fences.values():
            positions.sort()
        # Assignment stores happen after their RHS, including wrapped producers.
        for node in self.scopes.nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            scope = self.scopes.scope(node)
            if (
                isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.value is not None
                and dotted(node.value)
            ):
                for target in targets:
                    if (
                        isinstance(target, ast.Name)
                        and self.scopes.owner(target.id, node) is scope
                    ):
                        self.aliases[
                            scope,
                            target.id,
                            (node.end_lineno or node.lineno, node.end_col_offset or 0),
                        ] = node.value
            if not any(dotted(target) in bindings for target in targets):
                continue
            value = node.value
            if value is not None:
                for child in ast.walk(value):
                    if isinstance(child, ast.Call):
                        self.initial_stores.setdefault(child, set()).add(
                            (node.end_lineno or node.lineno, node.end_col_offset or 0)
                        )
        for call in self.analyzed_calls:
            position = (call.node.lineno, call.node.col_offset)
            receiver = (
                dotted(call.node.func.value)
                if isinstance(call.node.func, ast.Attribute)
                else ""
            )
            names = {owners.sandboxes.get(position, "")}
            # Each prefix can be a sandbox binding; services belong to that sandbox.
            parts = receiver.split(".")
            names.update(
                ".".join(parts[:length]) for length in range(1, len(parts) + 1)
            )
            if call.kind == "client" or call.kind.startswith("client."):
                for argument in [
                    *call.node.args,
                    *(keyword.value for keyword in call.node.keywords),
                ]:
                    names.update((dotted(argument), owners.argument_owner(argument)))
                    owner, binding = self.binding(dotted(argument), argument)
                    if (
                        binding in bindings
                        and binding != dotted(argument)
                        and self.scopes.deferred(argument, owner)
                    ):
                        owners.arguments[
                            argument.lineno,
                            argument.col_offset,
                            argument.end_lineno,
                            argument.end_col_offset,
                        ] = binding
            for original in names:
                owner, name = self.binding(original, call.node)
                if name not in bindings:
                    continue
                member = call
                if not call.kind and self.scopes.deferred(call.node, owner):
                    # Only captured receivers rooted in an actual producer qualify.
                    kind = "sandbox"
                    for attribute in (
                        receiver.removeprefix(original).strip(".").split(".")
                        if receiver != original
                        else []
                    ):
                        kind = next(
                            (
                                key
                                for key, definition in flow.resolver.mapping.receivers.items()
                                if definition.parent == kind
                                and definition.attr == attribute
                            ),
                            "",
                        )
                    if receiver == original or receiver.startswith(original + "."):
                        member = replace(call, kind=kind, receiver=receiver)
                        if kind.startswith("sandbox") and name != original:
                            owners.sandboxes[position] = name
                self.calls.setdefault((owner, name), []).append(member)
        self.members = {
            producer.node: self.collect(producer) for producer in self.producers
        }

    def binding(self, name: str, node: ast.expr) -> tuple[ast.AST, str]:
        """Follow local alias stores at their read positions, respecting write fences."""
        current_name, current = name, node
        seen: set[tuple[ast.AST, str, tuple[int, int]]] = set()
        while True:
            root, separator, tail = current_name.partition(".")
            owner = self.scopes.owner(root, current)
            positions = self.fences.get((owner, root), [])
            offset = bisect_right(positions, (current.lineno, current.col_offset))
            if not offset:
                return owner, current_name
            key = (owner, root, positions[offset - 1])
            value = self.aliases.get(key)
            if value is None or key in seen:
                return owner, current_name
            seen.add(key)
            current_name = dotted(value) + separator + tail
            current = value

    def workflow_calls(self, producer: Call) -> tuple[Call, ...]:
        """Return cached membership shared by headers and persistence checks."""
        return self.members.get(producer.node, ())

    def collect(self, producer: Call) -> tuple[Call, ...]:
        """Look up this binding's direct window plus its deferred captured calls."""
        owner = self.scopes.owner(producer.binding.split(".")[0], producer.node)
        start = (
            producer.node.end_lineno or producer.node.lineno,
            producer.node.end_col_offset or producer.node.col_offset,
        )
        parts = producer.binding.split(".")
        names = [
            "*",
            *(".".join(parts[:length]) for length in range(1, len(parts) + 1)),
        ]
        ends: list[tuple[int, int]] = []
        for name in names:
            positions = self.fences.get((owner, name), [])
            offset = bisect_right(positions, start)
            while offset < len(positions) and positions[
                offset
            ] in self.initial_stores.get(producer.node, set()):
                offset += 1
            if offset < len(positions):
                ends.append(positions[offset])
        end = min(ends) if ends else None
        return tuple(
            call
            for call in self.calls.get((owner, producer.binding), [])
            if call.node is not producer.node
            and (
                self.scopes.deferred(call.node, owner)
                or (
                    start <= (call.node.lineno, call.node.col_offset)
                    and (end is None or (call.node.lineno, call.node.col_offset) < end)
                )
            )
        )

    def suggestion_calls(self) -> tuple[Call, ...]:
        """Add captured receiver kinds only at call sites owned by a workflow."""
        captured: dict[ast.Call, Call] = {}
        for call in self.producers:
            for member in self.workflow_calls(call):
                if member.kind and not member.kind.startswith("client"):
                    captured[member.node] = member
        return tuple(captured.get(call.node, call) for call in self.analyzed_calls)
