# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Assignment effects used to fence uncertain expression/iteration evaluation."""

from __future__ import annotations

import ast
import sys

from .bindings import indirect_writes
from .proof_mutations import CONTAINER_WRITE, mutates_container
from .resolution import dotted


def stored_names(
    node: ast.AST, aliases: dict[str, str] | None = None
) -> frozenset[str]:
    """Include deferred stores conservatively: closures may run on a later iteration."""
    names: set[str] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            or isinstance(child, ast.Subscript)
            and isinstance(child.ctx, (ast.Store, ast.Del))
        ):
            names.update(indirect_writes(child, aliases))
        if mutates_container(child):
            names.add(CONTAINER_WRITE)
        if isinstance(child, (ast.Name, ast.Attribute)) and isinstance(
            child.ctx, (ast.Store, ast.Del)
        ):
            if name := dotted(child):
                names.add(name)
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in child.names
            )
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        if isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        # Pattern nodes are unavailable on the supported Python 3.9 floor.
        if sys.version_info >= (3, 10):
            if isinstance(child, (ast.MatchAs, ast.MatchStar)) and child.name:
                names.add(child.name)
            if isinstance(child, ast.MatchMapping) and child.rest:
                names.add(child.rest)
    return frozenset(names)
