# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Token-based source boundaries, including notebook-only line syntax."""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterator


def single_line(text: str) -> str:
    """Render source-derived comment text without physical line continuations."""
    return " ".join(
        text.replace("\\\r\n", "").replace("\\\n", "").replace("\\\r", "").split()
    )


def source_lines(source: str) -> list[str]:
    """Split on tokenizer newlines, not Unicode separators inside string data."""
    return io.StringIO(source, newline="").readlines()


def python_tokens(source: str) -> Iterator[tokenize.TokenInfo]:
    """Yield tokens outside strings; leave invalid suffixes untouched."""
    string_depth = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(source, newline="").readline):
            # Python 3.12+ exposes interpolated-string internals as separate tokens.
            name = tokenize.tok_name[token.type]
            if name in {"FSTRING_START", "TSTRING_START"}:
                string_depth += 1
            if not string_depth:
                yield token
            if name in {"FSTRING_END", "TSTRING_END"}:
                string_depth -= 1
    except (tokenize.TokenError, IndentationError):
        return


def assignment_magic_prefix(line: str) -> str | None:
    """Recognize a simple or annotated IPython assignment before its opaque RHS."""
    match = re.match(r"^([ \t]*[^\W\d]\w*(?:[ \t]*:[^=\r\n]+)?[ \t]*=[ \t]*)[!%]", line)
    return match[1] if match else None


def magic_lines(source: str) -> frozenset[int]:
    """Find IPython syntax outside strings, ignoring magic payload syntax."""
    lines = source_lines(source)
    candidates: dict[int, int] = {}
    for number, line in enumerate(lines, 1):
        if line.lstrip(" \t").startswith(("%", "!", "?")):
            candidates[number] = len(line) - len(line.lstrip(" \t"))
        elif prefix := assignment_magic_prefix(line):
            candidates[number] = len(prefix)
        elif line.rstrip().endswith("?"):
            candidates[number] = len(line.rstrip().rstrip("?"))
    found: set[int] = set()
    while candidates:
        for token in python_tokens("".join(lines)):
            row, column = token.start
            if (
                row in candidates
                and token.type in (tokenize.OP, tokenize.ERRORTOKEN)
                and column == candidates[row]
                and token.string in {"%", "!", "?"}
            ):
                found.add(row)
                del candidates[row]
                # Restart before lexing a magic's arbitrary quotes or shell payload.
                line = lines[row - 1]
                lines[row - 1] = "#" + line.rstrip("\r\n") + "\n"
                break
        else:
            break
    return frozenset(found)
