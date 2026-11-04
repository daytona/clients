# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Syntactic names used by expression inference and its consumers."""

from __future__ import annotations

import ast
import re


def dotted(node: ast.AST) -> str:
    # Python 3.9 compatibility precludes structural pattern matching.
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def looks_like_client(source: str) -> bool:
    """Name-only review requires Daytona identity, not a generic client spelling."""
    return bool(re.search("daytona", source, re.I)) and not re.search(
        "sandbox", source, re.I
    )
