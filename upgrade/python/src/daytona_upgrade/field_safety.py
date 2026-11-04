# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Opaque class lifecycle hooks demote field proofs, not linked provenance."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .resolution import Resolver


def replaced_field_hooks(owner: str, mutations: set[str]) -> bool:
    return any(
        owner + "." + hook in mutations
        for hook in (
            "__init__",
            "__getattribute__",
            "__getattr__",
            "__setattr__",
            "__delattr__",
            "*",
        )
    )


def class_mutations(module: ast.Module, aliases: dict[str, str]) -> set[str]:
    """Expand syntactic owner aliases for fences, never for positive proofs."""
    from .bindings import indirect_writes
    from .resolution import dotted

    mutations = {
        name
        for node in ast.walk(module)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        for name in (
            dotted(node),
            *([node.attr] if dotted(node.value) != "self" else []),
        )
    }
    mutations.update(
        name
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        for name in indirect_writes(node, aliases)
    )
    for name in tuple(mutations):
        root, separator, tail = name.partition(".")
        if separator:
            mutations.update(
                owner + "." + tail
                for key, owner in aliases.items()
                if key.startswith("@owner:" + root + ":")
            )
    return mutations


def opaque_fields(kind: str) -> bool:
    """Class metadata includes project bases, so inherited hooks fence reads too."""
    from .field_arguments import ARGUMENTS

    members = kind.partition(ARGUMENTS)[0].rsplit(":", 1)[-1].split(",")
    return bool(
        {
            "__getattribute__",
            "__getattr__",
            "__setattr__",
            "__delattr__",
            "@fields-unsafe",
        }.intersection(members)
    )


def intercepted_member(
    node: ast.Attribute, resolver: Resolver, env: dict[str, str]
) -> str | None:
    """Intercept exact self bindings as well as class-summary instance lookups."""
    from .attribute_proofs import _review_only
    from .field_arguments import argument_member
    from .resolution import dotted

    parent = resolver.infer(node.value, env)
    if not parent.startswith("instance:"):
        return None
    owner = parent.split(":")[1]
    if not (opaque_fields(parent) or env.get(owner + ".@unsafe-fields")):
        return None
    value = env.get(dotted(node), env.get(owner + "." + node.attr, ""))
    argument = argument_member(parent, node.attr)
    return _review_only(value.removeprefix("property:")) or _review_only(argument or "")
