# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read literal __all__ declarations without executing project code."""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class UnresolvedExports:
    """Possible module bindings, never importable receiver proofs."""

    names: frozenset[str]


def declared_exports(source: str) -> frozenset[str] | UnresolvedExports | None:
    """Separate absent, literal (including empty), and unresolved declarations."""
    from ._scopes import ScopeWrites

    try:
        module = ast.parse(source)
    except SyntaxError:
        return frozenset()
    names: frozenset[str] | None = None
    declared = False
    for statement in module.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in targets
            ):
                declared = True
                value = literal_names(statement.value)
                names = (
                    names | value
                    if isinstance(statement, ast.AugAssign)
                    and isinstance(statement.op, ast.Add)
                    and names is not None
                    and value is not None
                    else None
                    if isinstance(statement, ast.AugAssign)
                    else value
                )
                continue
        if any(
            isinstance(node, ast.Name) and node.id == "__all__"
            for node in ast.walk(statement)
        ):
            # Conditional assignments and mutations cannot establish a definite set.
            declared = True
            names = None
    if declared and names is None:
        writes = ScopeWrites()
        writes.visit(module)
        return UnresolvedExports(
            frozenset(name for name, _ in writes.events if name.isidentifier())
        )
    return names


def literal_names(node: ast.AST | None) -> frozenset[str] | None:
    """Resolve list/tuple literals and concatenation; never guess dynamic membership."""
    if isinstance(node, (ast.List, ast.Tuple)) and all(
        isinstance(element, ast.Constant) and isinstance(element.value, str)
        for element in node.elts
    ):
        return frozenset(
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_names(node.left), literal_names(node.right)
        return left | right if left is not None and right is not None else None
    return None
