# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Snapshot static setup values at assignment time, retaining keyword floors."""

from __future__ import annotations

from dataclasses import dataclass

import libcst as cst


@dataclass(frozen=True)
class SetupValue:
    """Immutable proof snapshot; Python 3.9 precludes dataclass slots."""

    strings: tuple[cst.SimpleString, ...] | None
    floors: tuple[str | None, ...] | None = None
    text: str | None = None


def setup_value(
    expression: cst.BaseExpression, assignments: dict[str, SetupValue | None]
) -> SetupValue | None:
    """Resolve only literals and saved bindings, never executing project code."""
    # Python 3.9 precludes structural matching of LibCST node variants.
    if isinstance(expression, cst.SimpleString):
        text = expression.evaluated_value
        return SetupValue((expression,), text=text if isinstance(text, str) else None)
    if isinstance(expression, cst.Name):
        return assignments.get(expression.value)
    if not isinstance(expression, (cst.List, cst.Tuple, cst.Dict)):
        return None
    strings: list[cst.SimpleString] = []
    complete = True
    floors: list[str | None] = []
    for element in expression.elements:
        if not isinstance(
            element, (cst.Element, cst.DictElement, cst.StarredDictElement)
        ):
            return None
        value = setup_value(element.value, assignments)
        if value is None or value.strings is None:
            complete = False
        else:
            strings.extend(value.strings)
        if isinstance(element, cst.StarredDictElement):
            floors.extend(
                value.floors if value and value.floors is not None else [None]
            )
        elif isinstance(element, cst.DictElement):
            key = element.key
            if not isinstance(key, cst.SimpleString):
                floors.append(None)
            elif key.evaluated_value == "python_requires":
                floors.append(value.text if value is not None else None)
    return SetupValue(
        tuple(strings) if complete else None,
        tuple(floors) if isinstance(expression, cst.Dict) else None,
    )
