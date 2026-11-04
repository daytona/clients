# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Syntactic abrupt exits, without assuming loop execution or exception behavior."""

from __future__ import annotations

import ast


def falls_through(statements: list[ast.stmt]) -> bool:
    """A direct exit or two exiting if arms cannot reach the enclosing suite tail."""
    for statement in statements:
        # Keep AST predicates rather than match syntax for Python 3.9 support.
        if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return False
        if isinstance(statement, ast.If) and not (
            falls_through(statement.body) or falls_through(statement.orelse)
        ):
            return False
    return True
