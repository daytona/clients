# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client evidence from augmented assignments, never an operator result proof."""

from __future__ import annotations

import ast
from collections.abc import Callable

from .collection_proofs import iterable_element, literal_collection_kind


def _element_evidence(node: ast.AST, infer: Callable[[ast.AST], str]) -> str:
    """Retain conditional value branches, never evidence from just the condition."""
    # AST predicates preserve the Python 3.9 floor (no match/case).
    if isinstance(node, ast.IfExp):
        return (
            "unproven:client"
            if any(
                _element_evidence(branch, infer) in {"client", "unproven:client"}
                for branch in (node.body, node.orelse)
            )
            else ""
        )
    return infer(node)


def augmented_kind(
    previous: str, node: ast.AugAssign, infer: Callable[[ast.AST], str]
) -> str:
    """An in-place protocol may replace its receiver or introduce client elements."""
    if previous in {"client", "unproven:client"} or previous.startswith(
        ("client-subclass:", "self-subclass:")
    ):
        return "unproven:client"
    if isinstance(node.op, ast.Add):
        # Literal RHS evidence remains useful even after aliased container writes
        # fence exact collection proofs. It can only request review, never rewrite.
        added = literal_collection_kind(node.value, infer) or infer(node.value)
        elements = (
            node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else ()
        )
        if any(
            iterable_element(kind) in {"client", "unproven:client"}
            for kind in (previous, added)
        ) or any(
            _element_evidence(element, infer) in {"client", "unproven:client"}
            for element in elements
        ):
            return "client-sequence"
    return ""
