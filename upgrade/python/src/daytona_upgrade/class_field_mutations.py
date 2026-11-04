# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Qualified class-owner fences shared by project indexing and field imports."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .attribute_proofs import _review_only
from .bindings import indirect_writes
from .field_safety import replaced_field_hooks
from .resolution import dotted

if TYPE_CHECKING:
    from .flow import Flow


def record_class_mutations(node: ast.AST, flow: Flow) -> None:
    """Resolve writes while their lexical class aliases are still in scope."""
    names: tuple[str, ...] = ()
    # AST dispatch deliberately keeps the tool's Python 3.9 syntax floor.
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        names = (dotted(node),)
    elif isinstance(node, ast.Call) or (
        isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del))
    ):
        names = indirect_writes(node, flow.reflective_aliases)
    for name in names:
        owner, separator, member = name.rpartition(".")
        if not separator or not owner:
            continue
        kind = flow.resolver.infer(ast.parse(owner, mode="eval").body, flow.env)
        if kind.startswith("class:"):
            canonical = kind.split(":")[1]
            if "." not in canonical:
                canonical = flow.context.module + "." + canonical
            flow.owner_mutations.add(canonical + "." + member)


def demote_class_fields(
    bindings: dict[str, str], owner: str, mutations: frozenset[str]
) -> None:
    """Mutate field metadata in place, preserving links but never stale proofs."""
    affected = {
        name.removeprefix(owner + ".")
        for name in mutations
        if name.startswith(owner + ".")
    }
    opaque = replaced_field_hooks(owner, set(mutations))
    for name, kind in tuple(bindings.items()):
        if not name.startswith(owner + "."):
            continue
        field = name.removeprefix(owner + ".")
        if field.startswith("@") and not field.startswith(
            ("@factory.", "@argument-safe.")
        ):
            continue
        member = field.rsplit(".", 1)[-1]
        if opaque or member in affected:
            descriptor = "property:" if kind.startswith("property:") else ""
            bindings[name] = descriptor + _review_only(kind.removeprefix("property:"))
