# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Optional v0 arguments in a v2 form: ``[[name@index|text]]``.

The ``text`` is rendered, with every ``$`` replaced by the argument's source,
only when the 0.x call passed that argument (the keyword ``name``, or the
positional ``index``); otherwise the marker is dropped and v2's default holds,
so the suggestion never names a variable the call did not have. A marker
may add ``|literal`` and ``|zero`` texts: for a numeric literal argument the
value is computed instead, ``literal`` with ``$`` as the literal (``zero``
when it is 0; ``literal`` again when no ``zero`` text is given). The
TypeScript tool reads the same markers (optional-arguments.ts).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

_MARKER = re.compile(r"\[\[(\w+)@(\d+)\|((?:(?!\]\]).)*)\]\]")
# A decimal numeric literal, written as both languages write one.
_NUMERIC_LITERAL = re.compile(r"(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?")


def render_optional_arguments(
    template: str, argument: Callable[[str, int], str | None]
) -> str:
    def replace(match: re.Match[str]) -> str:
        value = argument(match.group(1), int(match.group(2)))
        if value is None:
            return ""
        text, *rest = match.group(3).split("|")
        chosen = text
        if rest and _NUMERIC_LITERAL.fullmatch(value):
            literal = rest[0]
            zero = rest[1] if len(rest) > 1 else literal
            chosen = zero if float(value.replace("_", "")) == 0 else literal
        return chosen.replace("$", value)

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
