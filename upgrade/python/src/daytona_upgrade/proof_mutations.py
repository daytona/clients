# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative container-element proof invalidation without alias identities."""

from __future__ import annotations

import ast
from typing import Final

from .bindings import indirect_writes
from .bindings import invalidate as invalidate_binding

CONTAINER_WRITE: Final = "@container-write"
MUTATORS: Final = frozenset(
    {
        "append",
        "extend",
        "insert",
        "clear",
        "pop",
        "remove",
        "reverse",
        "sort",
        "add",
        "update",
        "discard",
        "difference_update",
        "intersection_update",
        "symmetric_difference_update",
        "__ior__",
        "__iand__",
        "__ixor__",
        "__isub__",
        "__setitem__",
        "__delitem__",
        "__iadd__",
        "__imul__",
        "__init__",
    }
)


def mutates_container(node: ast.AST, *, trusted_reflective: bool = False) -> bool:
    """Recognize writes even when their receiver's alias cannot be resolved."""
    # Boolean predicates retain the package's Python 3.9 syntax floor.
    if trusted_reflective and indirect_writes(node):
        return False
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, (ast.Store, ast.Del))
        # Instance-dictionary stores replace attributes, like setattr, but only
        # once the caller proved the builtin (AttributeProofs.trusted).
        and not (trusted_reflective and indirect_writes(node))
        or isinstance(node, ast.AugAssign)
        or isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in MUTATORS
    )


def invalidate(env: dict[str, str], name: str) -> None:
    """Drop all homogeneous-container proofs on a possible aliased write.

    Receiver kinds encode types, not identities. Killing every container proof
    deliberately trades precision for safety, including attribute/import aliases.
    Standalone client objects remain valid when a container changes its contents.
    """
    if name == CONTAINER_WRITE:
        env[CONTAINER_WRITE] = "mutated"
        for key, kind in list(env.items()):
            if key.endswith(".@dict") or ".@dict." in key or kind.startswith("items:"):
                invalidate_binding(env, key)
    else:
        invalidate_binding(env, name)


def shadowed_reflective(module: ast.AST) -> frozenset[str]:
    """Reflective builtins rebound anywhere in a module (syntactic, conservative)."""
    names = {"vars", "setattr", "object"}
    bound: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return frozenset(bound & names)
