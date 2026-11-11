# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Pattern compilation for the CLI-compatible BuildKit ignore matcher."""

from __future__ import annotations

import posixpath
import re
import warnings
from dataclasses import dataclass, field
from enum import Enum

from typing_extensions import assert_never

from ._dockerignore_error import PatternError
from ._dockerignore_nfa import Program, compile_program
from ._dockerignore_regexp import _go_regexp


def clean(text: str) -> str:
    """Use Go path.Clean semantics (including collapsing a double leading slash)."""
    return posixpath.normpath(text).replace("//", "/")


def _match_chunk(chunk: str, name: str) -> tuple[str, bool]:
    """Validate a Go filepath.Match chunk even after its match has failed.

    Walks the chunk by index: slicing it per character made a long chunk
    quadratic (TypeScript walks it the same way)."""
    at, left, failed = 0, 0, False

    def escaped() -> str:
        nonlocal at
        if at >= len(chunk) or chunk[at] in "-]":
            raise PatternError(chunk[at:], "syntax error in pattern")
        if chunk[at] == "\\":
            at += 1
        if len(chunk) - at < 2:
            raise PatternError(chunk[at:], "syntax error in pattern")
        at += 1
        return chunk[at - 1]

    while at < len(chunk):
        failed = failed or left >= len(name)
        ch = chunk[at]
        at += 1
        if ch == "[":
            rune = name[left] if not failed else "\0"
            if not failed:
                left += 1
            negated = chunk.startswith("^", at)
            if negated:
                at += 1
            count, matched = 0, False
            while True:
                if chunk.startswith("]", at) and count:
                    at += 1
                    break
                lo = escaped()
                hi = lo
                if chunk.startswith("-", at):
                    at += 1
                    hi = escaped()
                matched = matched or lo <= rune <= hi
                count += 1
            failed = failed or matched == negated
        elif ch == "?":
            if not failed:
                failed = name[left] == "/"
                left += 1
        else:
            if ch == "\\":
                if at >= len(chunk):
                    raise PatternError("", "syntax error in pattern")
                ch = chunk[at]
                at += 1
            if not failed:
                failed = ch != name[left]
                left += 1
    return name[left:], not failed


def validate_pattern(text: str) -> None:
    """Port filepath.Match(text, '.'); later chunks may never be validated."""
    remaining, name = text, "."
    while remaining:
        star = remaining.startswith("*")
        remaining = remaining.lstrip("*")
        index, in_range = 0, False
        while index < len(remaining):
            ch = remaining[index]
            if ch == "\\" and index + 1 < len(remaining):
                index += 2
                continue
            if ch == "[":
                in_range = True
            if ch == "]":
                in_range = False
            if ch == "*" and not in_range:
                break
            index += 1
        chunk, remaining = remaining[:index], remaining[index:]
        if star and not chunk:
            return
        tail, ok = _match_chunk(chunk, name)
        if ok and (not tail or remaining):
            name = tail
            continue
        if star and name:
            tail, ok = _match_chunk(chunk, "")
            if ok and (not tail or remaining):
                name = tail
                continue
        return


class _Kind(Enum):
    EXACT = 0
    PREFIX = 1
    SUFFIX = 2
    REGEXP = 3


@dataclass(frozen=True, slots=True)
class Pattern:
    text: str
    exclusion: bool
    kind: _Kind
    program: Program = field(default_factory=lambda: Program(()))
    error: PatternError | None = None

    def matches(self, rel: str) -> bool:
        match self.kind:
            case _Kind.EXACT:
                return rel == self.text
            case _Kind.PREFIX:
                return rel.startswith(self.text.removesuffix("**"))
            case _Kind.SUFFIX:
                # Go slices strings by byte, even across a retained UTF-8 BOM.
                suffix = self.text.encode("utf-8")[2:]
                name = rel.encode("utf-8")
                return name.endswith(suffix) or (suffix.startswith(b"/") and name == suffix[1:])
            case _Kind.REGEXP:
                return self.program.matches(rel)
            case unreachable:
                assert_never(unreachable)


def compile_pattern(text: str, raw: str, exclusion: bool) -> Pattern:
    """Compile as patternmatcher does, preserving its prefix/suffix shortcuts."""
    expression, kind = "^", _Kind.EXACT
    runes = text.removeprefix("\ufeff")
    index, token = 0, 0
    while index < len(runes):
        ch = runes[index]
        if ch == "*" and index + 1 < len(runes) and runes[index + 1] == "*":
            index += 1
            if index + 1 < len(runes) and runes[index + 1] == "/":
                index += 1
            if index + 1 == len(runes):
                if kind is _Kind.EXACT:
                    kind = _Kind.PREFIX
                else:
                    expression += ".*"
                    kind = _Kind.REGEXP
            else:
                expression += "(.*/)?"
                kind = _Kind.REGEXP
            if token == 0:
                kind = _Kind.SUFFIX
        elif ch == "*":
            expression += "[^/]*"
            kind = _Kind.REGEXP
        elif ch == "?":
            expression += "[^/]"
            kind = _Kind.REGEXP
        elif ch in ".+()|{}$":
            expression += "\\" + ch
        elif ch == "\\":
            if index + 1 < len(runes):
                index += 1
                expression += "\\" + runes[index]
                kind = _Kind.REGEXP
            else:
                expression += "\\\\"
        else:
            expression += ch
            if ch in "[]":
                kind = _Kind.REGEXP
        index += 1
        token += 1
    if kind is not _Kind.REGEXP:
        return Pattern(text, exclusion, kind)
    try:
        source = _go_regexp(expression + "$")
        # Validation only (matching runs on the linear program): keep every
        # existing compile error, but not re's FutureWarnings about set
        # syntax, which `-W error` would turn into a raw exception.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            re.compile(source, re.ASCII)
    except (re.error, PatternError):
        return Pattern(text, exclusion, kind, error=PatternError(raw, "invalid regular expression"))
    return Pattern(text, exclusion, kind, program=compile_program(source))
