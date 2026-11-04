# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Join fallback values without promoting a mixed or short-circuit result."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .annotation_exports import joined_bindings
from .expression_names import dotted

if TYPE_CHECKING:
    from .resolution import Resolver


def fallback_kind(
    node: ast.IfExp | ast.BoolOp, resolver: Resolver, env: dict[str, str]
) -> str:
    # AST dispatch retains the tool's Python 3.9 syntax floor.
    values = [node.body, node.orelse] if isinstance(node, ast.IfExp) else node.values
    kinds = [resolver.infer(value, env) for value in values]
    joined: dict[str, str] | None = None
    for kind in kinds:
        joined = joined_bindings(resolver, (joined, {"value": kind}))
    result = (joined or {}).get("value", "")
    if result != "client":
        return result
    if all(nonnull_client(value, resolver, env) for value in values):
        return "client"
    if isinstance(node, ast.BoolOp):
        return "client" if isinstance(node.op, ast.Or) else "unproven:client"
    test = node.test
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], (ast.Is, ast.IsNot))
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
        and ast.dump(test.left)
        == ast.dump(node.body if isinstance(test.ops[0], ast.IsNot) else node.orelse)
    ):
        return "client"
    return "unproven:client"


def nonnull_client(node: ast.AST, resolver: Resolver, env: dict[str, str]) -> bool:
    """Nullable annotations share the client kind, but are not non-null evidence."""
    if resolver.infer(node, env) != "client":
        return False
    if isinstance(node, (ast.Name, ast.Attribute)):
        return env.get(dotted(node) + ".@nonnull") == "client"
    if isinstance(node, ast.Call):
        return resolver.infer(node.func, env) == "type:client"
    if isinstance(node, ast.BoolOp):
        return (
            nonnull_client(node.values[-1], resolver, env)
            if isinstance(node.op, ast.Or)
            else all(nonnull_client(value, resolver, env) for value in node.values)
        )
    if isinstance(node, ast.IfExp):
        return nonnull_client(node.body, resolver, env) and nonnull_client(
            node.orelse, resolver, env
        )
    return False


def required_client_annotation(
    node: ast.AST | None, resolver: Resolver, env: dict[str, str]
) -> bool:
    """Only direct client types exclude None; nullable/opaque aliases do not."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return False
    return node is not None and resolver.infer(node, env) == "type:client"
