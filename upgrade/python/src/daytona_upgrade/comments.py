# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Own-line comment insertion, preserving customer comments and whitespace."""

from __future__ import annotations

import tokenize
from collections.abc import Sequence

import libcst as cst

from .lexical import python_tokens, single_line, source_lines
from .models import Mode


def remove_suggestions(source: str) -> str:
    """Remove only standalone tool comment tokens, never literal string data."""
    lines = source_lines(source)
    removed = {
        token.start[0]
        for token in python_tokens(source)
        if token.type == tokenize.COMMENT
        and token.string.startswith("# daytona-v2:")
        and not lines[token.start[0] - 1][: token.start[1]].strip()
    }
    return "".join(line for row, line in enumerate(lines, 1) if row not in removed)


def leading_comments(
    existing: Sequence[cst.EmptyLine], messages: list[str], prefix: tuple[Mode, str]
) -> list[cst.EmptyLine]:
    mode, source_prefix = prefix
    prior = "\n".join(line.comment.value for line in existing if line.comment)
    normalized = [
        single_line(message) if "\n" in message or "\r" in message else message
        for message in messages
    ]
    return [
        *existing,
        *(
            cst.EmptyLine(comment=cst.Comment("# " + text))
            for text in (
                normalized if mode == "suggest-v2" else dict.fromkeys(normalized)
            )
            if text not in prior and (mode != "upgrade" or text not in source_prefix)
        ),
    ]
