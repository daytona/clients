# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Identify setup.py bindings invalidated by conditional or deferred execution."""

from __future__ import annotations

from collections.abc import Mapping

import libcst as cst
from libcst.metadata import ExpressionContext


def mutation_name(
    node: cst.CSTNode, contexts: Mapping[cst.CSTNode, ExpressionContext]
) -> str:
    """Identify possible container mutations without executing arbitrary methods."""
    if isinstance(node, cst.AugAssign):
        root = node.target
    elif isinstance(node, cst.Call) and isinstance(node.func, cst.Attribute):
        root = node.func.value
    elif isinstance(node, (cst.Attribute, cst.Subscript)) and contexts.get(node) in (
        ExpressionContext.STORE,
        ExpressionContext.DEL,
    ):
        root = node.value
    else:
        return ""
    while isinstance(root, (cst.Attribute, cst.Subscript)):
        root = root.value
    return root.value if isinstance(root, cst.Name) else ""


class Writes(cst.CSTVisitor):
    """Collect writes from metadata without interpreting a compound statement."""

    def __init__(self, contexts: Mapping[cst.CSTNode, ExpressionContext]) -> None:
        self.contexts = contexts
        self.names: set[str] = set()

    def visit_Name(self, node: cst.Name) -> None:
        if self.contexts.get(node) in (ExpressionContext.STORE, ExpressionContext.DEL):
            self.names.add(node.value)

    def on_visit(self, node: cst.CSTNode) -> bool:
        name = mutation_name(node, self.contexts)
        if name:
            self.names.add(name)
        return super().on_visit(node)
