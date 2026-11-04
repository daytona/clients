# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Historical SDK links are review evidence, not current receiver proofs."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .flow import Flow


def mock_linked(node: ast.AST, flow: Flow) -> bool:
    """Read lexical or imported history without promoting a replaced value."""
    from .resolution import dotted

    name = dotted(node)
    if not name:
        return False
    if flow.env.get(name + ".@mock-linked"):
        return True
    if isinstance(node, ast.Attribute):
        parent = flow.resolver.infer(node.value, flow.env)
        if parent.startswith(("module:", "local-module:")):
            module = parent.split(":", 1)[1]
            return bool(
                flow.context.index.exports.get(module, {}).get(
                    node.attr + ".@mock-linked"
                )
            )
    return False
