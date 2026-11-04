# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Collection context for environment scalars, without interpreting YAML values."""

from __future__ import annotations

import re
from collections.abc import Sequence


def yaml_continuation(
    lines: Sequence[str], start: int, indent: int
) -> tuple[str | None, bool]:
    """Return one indented scalar continuation and whether it is ambiguous."""
    continuations: list[str] = []
    for following in lines[start:]:
        if not following.strip() or following.lstrip().startswith("#"):
            continue
        if len(following) - len(following.lstrip()) <= indent:
            break
        end = next(
            (
                token.start()
                for token in re.finditer(
                    r""""(?:\\.|[^"\\])*"|'[^']*'|(?P<comment>(?<!\S)\#)""",
                    following,
                )
                if token.group("comment")
            ),
            len(following),
        )
        continuations.append(following[:end].strip())
    if len(continuations) == 1:
        return continuations[0], False
    return None, len(continuations) > 1


def yaml_block_scalar(
    lines: Sequence[str], line_number: int, line: str
) -> tuple[bool, str | None]:
    """Parse a literal or folded block scalar, preserving only one-token values."""
    field_value = re.search(
        r":\s*((?:[&!]\S+\s+)*(?:[|>](?:[1-9][+-]?|[+-][1-9]?)?))\s*$",
        _without_comment(line),
    )
    marker = _block_marker(field_value[1]) if field_value else None
    if marker is None:
        return False, None
    style, modifiers = marker
    indent = len(line) - len(line.lstrip())
    explicit = re.search(r"[1-9]", modifiers)
    content_indent = indent + int(explicit[0]) if explicit else None
    content: list[str] = []
    for following in lines[line_number + 1 :]:
        current_indent = len(following) - len(following.lstrip())
        if not following.strip():
            if content_indent is not None:
                content.append("")
            continue
        if current_indent <= indent:
            break
        if content_indent is None:
            content_indent = current_indent
        if current_indent < content_indent:
            break
        content.append(following[content_indent:])
    scalar = "\n".join(content) if style == "|" else " ".join(content)
    if "+" in modifiers:
        scalar += "\n"
    elif "-" not in modifiers and scalar:
        scalar = scalar.rstrip("\n") + "\n"
    else:
        scalar = scalar.rstrip("\n")
    normalized = scalar.strip()
    return (
        True,
        normalized
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", normalized)
        else None,
    )


def yaml_block_scalar_bodies(lines: Sequence[str]) -> tuple[bool, ...]:
    """Mark block-scalar payload lines so they are not parsed as YAML keys."""
    bodies = [False] * len(lines)
    parent_indent: int | None = None
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        if parent_indent is not None:
            if not line.strip() or indent > parent_indent:
                bodies[index] = True
                continue
            parent_indent = None
        cleaned = _without_comment(line)
        if re.search(
            r":\s*(?:[&!]\S+\s+)*(?:[|>](?:[1-9][+-]?|[+-][1-9]?)?)\s*$", cleaned
        ):
            parent_indent = indent
    return tuple(bodies)


def _block_marker(value: str) -> tuple[str, str] | None:
    scalar = re.sub(r"^(?:\s*[&!]\S+\s+)+", "", value).strip()
    marker = re.fullmatch(r"([|>])((?:[1-9][+-]?|[+-][1-9]?)?)", scalar)
    return (marker[1], marker[2]) if marker else None


def _without_comment(line: str) -> str:
    comment = next(
        (
            token.start()
            for token in re.finditer(
                r""""(?:\\.|[^"\\])*"|'[^']*'|(?P<comment>(?<!\S)\#)""", line
            )
            if token.group("comment")
        ),
        len(line),
    )
    return line[:comment]


class YamlFlow:
    """Accumulate flow delimiters only at node starts, never inside plain text."""

    def __init__(self) -> None:
        self._closers: list[str] = []
        self._quote = ""
        self._block_indent: int | None = None
        self._pending_indent: int | None = None

    def line(self, text: str) -> tuple[bool, ...]:
        """Return flow membership at each column and retain multiline context."""
        flow = [False] * len(text)
        indent = len(text) - len(text.lstrip())
        if not text.strip() or (
            self._block_indent is not None and indent > self._block_indent
        ):
            return tuple(flow)
        self._block_indent = None
        scalar_indent = (
            self._pending_indent
            if self._pending_indent is not None and indent > self._pending_indent
            else indent
        )
        self._pending_indent = None
        ready = True
        plain = False
        index = 0
        while index < len(text):
            character = text[index]
            following = text[index + 1 : index + 2]
            flow[index] = bool(self._closers)
            if self._quote:
                if (self._quote == '"' and character == "\\") or (
                    self._quote == "'" and character == "'" and following == "'"
                ):
                    index += 1
                    if index < len(text):
                        flow[index] = bool(self._closers)
                elif character == self._quote:
                    self._quote = ""
            elif character.isspace():
                pass
            elif character == ":" and (
                not following
                or following.isspace()
                or (self._closers and following in "[{'\"")
            ):
                ready = True
                plain = False
                if not self._closers:
                    prefix = re.match(r"\s*(?:-\s+)*", text)
                    scalar_indent = prefix.end() if prefix else indent
            elif self._closers and character == ",":
                ready = True
            elif self._closers and character == self._closers[-1]:
                self._closers.pop()
                ready = False
                plain = False
            elif ready and character in "[{":
                self._closers.append("]" if character == "[" else "}")
            elif ready and character in "\"'":
                self._quote = character
                ready = False
            elif (
                ready and character == "-" and following.isspace() and not self._closers
            ):
                scalar_indent = index
            elif ready and character in "&!":
                prop = re.match(r"[&!]\S+\s+", text[index:])
                if prop:
                    end = index + prop.end()
                    flow[index:end] = [bool(self._closers)] * prop.end()
                    index = end - 1
                else:
                    ready = False
            else:
                ready = False
                plain = not self._closers
            index += 1
        if not self._closers:
            self._quote = ""
            if plain:
                self._block_indent = scalar_indent
            if ready:
                self._pending_indent = scalar_indent
        return tuple(flow)
