# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dockerfile text shared by declarative builders and local contexts."""

from __future__ import annotations

import re

from .errors import create_error

# Go's unicode.IsSpace (Latin-1 and the Unicode White_Space set).
_GO_SPACE = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
_DIRECTIVE = re.compile(r"([a-zA-Z][a-zA-Z0-9]*)[\t\n\f\r ]*=[\t\n\f\r ]*([^\n]+?)[\t\n\f\r ]*")


def dockerfile_escape(dockerfile: str) -> str:
    """The escape character BuildKit reads from the parser directives.

    A port of BuildKit's processLine + DirectiveParser.ParseLine (v0.31.2):
    lines as bufio.ScanLines splits them, a first-line BOM dropped, trailing
    CR/LF and leading Unicode space trimmed, a `#`, more leading space, then
    `name = value`; the first line that is not a known directive ends them.
    """
    escape = "\\"
    seen: set[str] = set()
    for index, line in enumerate(dockerfile.split("\n")):
        line = line.removeprefix("\ufeff") if index == 0 else line
        line = line.rstrip("\r\n").lstrip(_GO_SPACE)
        if not line.startswith("#"):
            break
        match = _DIRECTIVE.fullmatch(line[1:].lstrip(_GO_SPACE))
        if match is None:
            break
        name = match[1].lower()
        if name not in ("syntax", "escape", "check") or name in seen:
            break
        seen.add(name)
        if name == "escape":
            escape = match[2]
    return escape


def ends_with_continuation(dockerfile: str) -> bool:
    """Whether the last instruction line ends with the escape character.

    BuildKit joins such a line with the next one, so a step appended to the
    base would become part of its last instruction. A comment line ending in
    the escape character is not a continuation. TypeScript reads the same.
    """
    escape = dockerfile_escape(dockerfile)
    for line in reversed(dockerfile.split("\n")):
        text = line.rstrip(" \t\r")
        if not text or text.lstrip(" \t").startswith("#"):
            continue
        return text.endswith(escape)
    return False


def require_text(value: object, label: str, *, strip: bool = False) -> str:
    """A non-blank UTF-8 string, stripped only when rendered raw."""
    if not isinstance(value, str) or not value.strip():
        raise create_error(f"{label} must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    # A surrogate has no UTF-8 form; both SDKs refuse it rather than diverge.
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise create_error(f"{label} must not contain a lone surrogate", 400, "BAD_REQUEST", "SDK")
    return value.strip() if strip else value
