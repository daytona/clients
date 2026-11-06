# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Diagnose historical root names absent from the pinned v0 bundle."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .models import Item
from .module_proofs import V0_SUBMODULES

if TYPE_CHECKING:
    from .flow import Flow


def export_items(flow: Flow) -> list[Item]:
    """Check only migrated root names when the shared mapping supplies exports."""
    mapping = flow.resolver.mapping
    if mapping.v0_exports is None:
        return []
    known = {
        *mapping.v0_exports,
        *mapping.root_names,
        "v0",
        "v2",
        *V0_SUBMODULES,
    }
    items: list[Item] = []
    for node in ast.walk(ast.parse(flow.source)):
        names: list[tuple[str, int, int]] = []
        # Python 3.9 compatibility precludes structural pattern matching.
        if (
            isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module in mapping.modules
        ):
            names = [
                (alias.name, node.lineno, node.col_offset)
                for alias in node.names
                if alias.name != "*"
            ]
        elif isinstance(node, ast.Attribute):
            value = node.value
            kind = flow.kinds.get(
                (
                    value.lineno,
                    value.col_offset,
                    value.end_lineno or value.lineno,
                    value.end_col_offset or value.col_offset,
                ),
                "",
            )
            if kind in ("module:daytona", "module:daytona_sdk") and not (
                node.attr.startswith("__") and node.attr.endswith("__")
            ):
                names = [(node.attr, node.lineno, node.col_offset)]
        for name, line, column in names:
            if name not in known and not (
                name.startswith("__") and name.endswith("__")
            ):
                items.append(
                    Item(
                        file=flow.context.file,
                        line=line,
                        column=column + 1,
                        kind="private-import",
                        message=f"{name} is not exported by bundled v{mapping.v0_version}; use the v2 API or remove it. It is unavailable from daytona.v0.",
                    )
                )
    return items
