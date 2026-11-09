# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Optional v0 arguments in a v2 form: ``[[name@index|text]]``.

The ``text`` is rendered, with every ``$`` replaced by the argument's source,
only when the 0.x call passed that argument (the keyword ``name``, or the
positional ``index``); otherwise the marker is dropped and v2's default holds,
so the suggestion never names a variable the call did not have. The
TypeScript tool reads the same markers (optional-arguments.ts).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

_MARKER = re.compile(r"\[\[(\w+)@(\d+)\|((?:(?!\]\]).)*)\]\]")


def render_optional_arguments(
    template: str, argument: Callable[[str, int], str | None]
) -> str:
    def replace(match: re.Match[str]) -> str:
        value = argument(match.group(1), int(match.group(2)))
        return "" if value is None else match.group(3).replace("$", value)

    return _MARKER.sub(replace, template)


def call_argument(
    node: ast.Call, name: str, index: int, segment: Callable[[ast.expr], str | None]
) -> str | None:
    """The source of the argument ``node`` passes as ``name`` (keyword) or at
    ``index`` (positional), or ``None`` when it passes neither. A ``*args`` or
    ``**kwargs`` that could hold it renders the argument by its name."""
    for keyword in node.keywords:
        if keyword.arg == name:
            return segment(keyword.value) or name
    for position, value in enumerate(node.args):
        if isinstance(value, ast.Starred):
            return name
        if position == index:
            return segment(value) or name
    if any(keyword.arg is None for keyword in node.keywords):
        return name
    return None
