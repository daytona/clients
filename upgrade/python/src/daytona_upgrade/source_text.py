# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Immutable UTF-8 line offsets for constant-time AST source boundaries."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from itertools import accumulate

from .lexical import source_lines


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class SourceText:
    """Keep one encoded source and its parser-newline boundaries per analysis."""

    data: bytes
    offsets: tuple[int, ...]
    lines: tuple[str, ...]

    @classmethod
    def parse(cls, source: str) -> SourceText:
        lines = tuple(source_lines(source))
        offsets = tuple(
            accumulate((len(line.encode("utf-8")) for line in lines), initial=0)
        )
        return cls(source.encode("utf-8"), offsets, lines)

    def segment(self, node: ast.expr) -> str | None:
        """Match ast.get_source_segment's unpadded result without splitting again."""
        try:
            if node.end_lineno is None or node.end_col_offset is None:
                return None
            start = self.offsets[node.lineno - 1] + node.col_offset
            end = self.offsets[node.end_lineno - 1] + node.end_col_offset
        except AttributeError:
            return None
        return self.data[start:end].decode("utf-8")
