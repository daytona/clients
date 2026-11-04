# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""File-specific manifest parse failures without parser tracebacks."""

from __future__ import annotations

import sys
from pathlib import Path

from libcst import ParserSyntaxError

from .models import Item


def python_source_item(path: str, error: ParserSyntaxError | SyntaxError) -> Item:
    """Keep source parse diagnostics together, including interpreter limitations."""
    return Item(
        file=path,
        line=(error.lineno if isinstance(error, SyntaxError) else error.raw_line) or 0,
        column=0,
        kind="unsupported-file",
        message=(
            f"Cannot analyze this source while the tool is running on Python {sys.version_info.major}.{sys.version_info.minor}. "
            "Run the tool with Python >=3.10 (the 2.x SDK requires Python 3.10+) "
            "and an interpreter that supports this file's syntax."
            if isinstance(error, SyntaxError)
            else python_parse_message(error)
        ),
    )


def python_parse_message(error: ParserSyntaxError | SyntaxError) -> str:
    """Render parser details without a multiline source dump."""
    reason = error.msg if isinstance(error, SyntaxError) else error.message
    return f"Could not be parsed: {' '.join(reason.split())}"


class ManifestParseError(ValueError):
    """Keep exception traceback attributes writable for context managers."""

    def __init__(self, path: Path, reason: str, format: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason
        self.format = format

    def __str__(self) -> str:
        if self.format == "Python" and isinstance(
            self.__cause__, (ParserSyntaxError, SyntaxError)
        ):
            error = self.__cause__
            line = error.lineno if isinstance(error, SyntaxError) else error.raw_line
            reason = python_parse_message(error).removeprefix("Could not be parsed: ")
            return f"{self.path.as_posix()} could not be parsed: {reason} (line {line or 0})"
        return f"{self.path.as_posix()} is not valid {self.format}: {self.reason}; fix it, then rerun."
