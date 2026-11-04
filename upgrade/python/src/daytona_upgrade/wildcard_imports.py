# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Recognize adjacent SDK wildcard pairs without crossing a suite boundary."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import libcst as cst

from .imports import module_name


def paired_wildcard(
    node: cst.BaseSmallStatement, parent_of: Callable[[cst.CSTNode], cst.CSTNode]
) -> bool:
    """Avoid adding a second v0 star when upgrading an already-split import."""
    if (
        not isinstance(node, cst.ImportFrom)
        or module_name(node.module) != "daytona"
        or not isinstance(node.names, cst.ImportStar)
    ):
        return False
    parent = parent_of(node)
    siblings: Sequence[cst.CSTNode] = ()
    current: cst.CSTNode = node
    if isinstance(parent, (cst.SimpleStatementLine, cst.SimpleStatementSuite)):
        siblings = parent.body
        if len(siblings) == 1 and isinstance(parent, cst.SimpleStatementLine):
            container = parent_of(parent)
            if isinstance(container, (cst.Module, cst.IndentedBlock)):
                siblings, current = container.body, parent
    position = next((i for i, sibling in enumerate(siblings) if sibling is current), -1)
    if position < 0 or position + 1 == len(siblings):
        return False
    following = siblings[position + 1]
    if isinstance(following, cst.SimpleStatementLine):
        following = following.body[0]
    return (
        isinstance(following, cst.ImportFrom)
        and not following.relative
        and module_name(following.module) == "daytona.v0"
        and isinstance(following.names, cst.ImportStar)
    )
