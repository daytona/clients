# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Preserve member-replacement effects across class and function boundaries."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .flow import Flow


def instance_member_writes(node: ast.ClassDef, enclosing: Flow) -> set[str]:
    """Reuse class write proofs, including reflective stores and receiver aliases."""
    from .classflow import AttributeWrites
    from .member_mutations import replacement

    class RuntimeWrites(AttributeWrites):
        """Attribute declarations alone do not replace an inherited callable."""

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.value is not None:
                super().visit_AnnAssign(node)

    members: set[str] = set()
    for method in node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = [*method.args.posonlyargs, *method.args.args]
        if not positional:
            continue
        writes = RuntimeWrites(enclosing.resolver, enclosing.env)
        writes.class_scope = False
        writes.reflective_aliases = enclosing.reflective_aliases
        # This is a possible-write summary, not proof of instance binding.
        # Decorators may bind an instance or class, even when unknown to us.
        writes.env[positional[0].arg] = "instance-receiver"
        writes.visit_suite(method.body)
        for name, kinds in writes.writes.items():
            candidates = (
                enclosing.resolver.mapping.moved
                if name == "self"
                else (name.removeprefix("self."),)
            )
            for member in candidates:
                if any(
                    replacement(member, kind, enclosing.resolver.mapping)
                    for kind in kinds
                ):
                    members.add("@member-write." + member)
    return members


def member_write_effects(node: ast.Call, flow: Flow) -> tuple[str, ...]:
    """Snapshot a fetched helper's effects before arguments can rebind it."""
    from .resolution import dotted

    name = dotted(node.func)
    owner = flow.env.get(name + ".@owner", name)
    prefix = owner + "."
    return tuple(
        key.removeprefix(prefix)
        for key in flow.env
        if key.startswith(prefix + "@member-write.")
    )
