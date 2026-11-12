# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded-state matching of the ignore compiler's translated regexp subset."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from typing_extensions import assert_never


@dataclass(frozen=True, slots=True)
class _Token:
    kind: Literal["atom", "star", "directory"]
    expression: re.Pattern[str]


def _atom_end(source: str, start: int) -> int:
    """Find one validated literal, escape, class or zero-width assertion."""
    end = start + 1
    if source[start] == "[":
        if source[end] == "^":
            end += 1
        if source[end] == "]":
            end += 1
        while source[end] != "]":
            end += 2 if source[end] == "\\" else 1
        return end + 1
    if source[start] == "\\":
        widths = {"x": 2, "u": 4, "U": 8}
        return end + 1 + widths.get(source[end], 0)
    return end


@dataclass(frozen=True, slots=True)
class Program:
    tokens: tuple[_Token, ...]

    def matches(self, path: str) -> bool:
        """Propagate reachable offsets in O(path length × program size), no recursion.

        Regexes inspect only one character or one assertion at a fixed offset;
        repetitions are DP transitions, never backtracking regex operations.
        Two rows bound auxiliary memory to O(path length).
        """
        reachable = [True] + [False] * len(path)
        for token in self.tokens:
            following = [False] * (len(path) + 1)
            match token.kind:
                case "atom":
                    for offset, active in enumerate(reachable):
                        if active:
                            result = token.expression.match(path, offset)
                            if result is not None:
                                following[result.end()] = True
                case "star":
                    following[0] = reachable[0]
                    for offset, char in enumerate(path, 1):
                        following[offset] = reachable[offset] or (
                            following[offset - 1] and token.expression.fullmatch(char) is not None
                        )
                case "directory":
                    following[0] = reachable[0]
                    running = False
                    for offset, char in enumerate(path, 1):
                        running = (running or reachable[offset - 1]) and char != "\n"
                        following[offset] = reachable[offset] or (running and char == "/")
                case unreachable:
                    assert_never(unreachable)
            reachable = following
        # A final \\Q can quote the generated end anchor, leaving a prefix match.
        return any(reachable)


def compile_program(source: str) -> Program:
    """Tokenize already validated, translated source, including quoted literals."""
    tokens: list[_Token] = []
    index = 0
    while index < len(source):
        if source.startswith("(.*/)?", index):
            tokens.append(_Token("directory", re.compile(".")))
            index += len("(.*/)?")
            continue
        end = _atom_end(source, index)
        repeated = source[end : end + 1] == "*"
        tokens.append(_Token("star" if repeated else "atom", re.compile(source[index:end], re.ASCII)))
        index = end + int(repeated)
    return Program(tuple(tokens))
