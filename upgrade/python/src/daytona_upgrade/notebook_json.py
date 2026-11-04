# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Locate notebook source values and rewrite them without reserializing JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceValueSpan:
    """Character offsets delimiting one complete JSON cell source value."""

    start: int
    end: int


def _skip_space(source: str, start: int) -> int:
    position = start
    while position < len(source) and source[position] in " \t\r\n":
        position += 1
    return position


def _members(source: str, start: int) -> tuple[tuple[str, SourceValueSpan], ...]:
    """Read direct object members of an already validated JSON document."""
    decoder = json.JSONDecoder()
    position = _skip_space(source, start + 1)
    members: list[tuple[str, SourceValueSpan]] = []
    while source[position] != "}":
        key, key_end = decoder.raw_decode(source, position)
        value_start = _skip_space(source, _skip_space(source, key_end) + 1)
        _, value_end = decoder.raw_decode(source, value_start)
        members.append((key, SourceValueSpan(value_start, value_end)))
        position = _skip_space(source, value_end)
        if source[position] == ",":
            position = _skip_space(source, position + 1)
    return tuple(members)


def _elements(source: str, start: int) -> tuple[SourceValueSpan, ...]:
    """Read direct array elements, skipping their entire nested values."""
    decoder = json.JSONDecoder()
    position = _skip_space(source, start + 1)
    elements: list[SourceValueSpan] = []
    while source[position] != "]":
        _, end = decoder.raw_decode(source, position)
        elements.append(SourceValueSpan(position, end))
        position = _skip_space(source, end)
        if source[position] == ",":
            position = _skip_space(source, position + 1)
    return tuple(elements)


def cell_source_spans(source: str) -> tuple[SourceValueSpan | None, ...]:
    """Locate only direct cells[*].source values after notebook validation."""
    cells = next(
        span
        for key, span in reversed(_members(source, _skip_space(source, 0)))
        if key == "cells"
    )
    return tuple(
        next(
            (
                span
                for key, span in reversed(_members(source, cell.start))
                if key == "source"
            ),
            None,
        )
        for cell in _elements(source, cells.start)
    )


def _inline_separator(source: str, span: SourceValueSpan) -> str:
    """Infer a singleton array's separator from its containing cell object."""
    cells = next(
        value
        for key, value in reversed(_members(source, _skip_space(source, 0)))
        if key == "cells"
    )
    cell = next(
        cell
        for cell in _elements(source, cells.start)
        if cell.start < span.start < cell.end
    )
    members = _members(source, cell.start)
    if len(members) < 2:
        return ","
    comma = _skip_space(source, members[0][1].end)
    separator = source[comma : _skip_space(source, comma + 1)]
    return ", " if "\n" in separator or "\r" in separator else separator


def rewrite_source_value(source: str, span: SourceValueSpan, code: str) -> str:
    """Keep string/array shape, Unicode policy, and array whitespace conventions."""
    original = source[span.start : span.end]
    ensure_ascii = source.isascii()
    if original.startswith('"'):
        return json.dumps(code, ensure_ascii=ensure_ascii)
    elements = _elements(source, span.start)
    if not elements:
        return json.dumps(code.splitlines(keepends=True), ensure_ascii=ensure_ascii)
    prefix = source[span.start : elements[0].start]
    suffix = source[elements[-1].end : span.end]
    separators = tuple(
        source[left.end : right.start] for left, right in zip(elements, elements[1:])
    )
    separator = (
        separators[0]
        if separators
        else "," + prefix[1:]
        if "\n" in prefix or "\r" in prefix or prefix[1:]
        else _inline_separator(source, span)
    )
    parts = [prefix]
    for index, line in enumerate(code.splitlines(keepends=True)):
        if index:
            parts.append(
                separators[index - 1] if index <= len(separators) else separator
            )
        parts.append(json.dumps(line, ensure_ascii=ensure_ascii))
    parts.append(suffix)
    return "".join(parts)
