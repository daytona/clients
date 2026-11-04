# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal mapping expansion shared by factory provenance and source rewriting."""

from __future__ import annotations

import ast

import libcst as cst


def ast_mapping(value: ast.expr) -> dict[str, ast.expr] | None:
    """Resolve string-keyed literal merges; unknown expansions stay unresolved."""
    if not isinstance(value, ast.Dict):
        return None
    entries: dict[str, ast.expr] = {}
    for key, item in zip(value.keys, value.values):
        if key is None:
            nested = ast_mapping(item)
            if nested is None:
                return None
            entries.update(nested)
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            entries[key.value] = item
        else:
            return None
    return entries


def ast_options(values: list[ast.keyword]) -> dict[str, ast.expr] | None:
    """Inspect the last supplied value without relying on duplicate-key errors."""
    entries: dict[str, ast.expr] = {}
    for keyword in values:
        if keyword.arg is not None:
            entries[keyword.arg] = keyword.value
        else:
            nested = ast_mapping(keyword.value)
            if nested is None:
                return None
            entries.update(nested)
    return entries


def literal_elements(value: cst.BaseExpression) -> tuple[cst.DictElement, ...] | None:
    """Keep every literal entry in evaluation order, including shadowed entries."""
    if not isinstance(value, cst.Dict):
        return None
    entries: list[cst.DictElement] = []
    for element in value.elements:
        if isinstance(element, cst.StarredDictElement):
            nested = literal_elements(element.value)
            if nested is None:
                return None
            entries.extend(nested)
        elif (
            isinstance(element, cst.DictElement)
            and isinstance(element.key, cst.SimpleString)
            and isinstance(element.key.evaluated_value, str)
        ):
            entries.append(element)
        else:
            return None
    return tuple(entries)
