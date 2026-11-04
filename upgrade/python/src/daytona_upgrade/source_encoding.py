# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lossless source decoding with structured, actionable input diagnostics."""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass, replace
from pathlib import Path

from .models import Item


@dataclass(frozen=True)
class FileEncodingError(ValueError):
    """Identify undecodable input without exposing raw decoder exceptions."""

    path: Path
    encoding: str | None = "UTF-8"
    needs_declaration: bool = False

    def __str__(self) -> str:
        if self.needs_declaration:
            return (
                f"{self.path.as_posix()} is not valid UTF-8 and declares no source encoding; "
                "convert it to UTF-8 or add a coding declaration, then rerun."
            )
        if self.encoding is None:
            return (
                f"{self.path.as_posix()} has an invalid source encoding declaration; "
                "correct it or convert the file to UTF-8, then rerun."
            )
        return (
            f"{self.path.as_posix()} is not valid {self.encoding}; "
            "convert it to UTF-8, then rerun."
        )

    def item(self, base: Path) -> Item:
        """Use root-relative paths locally, absolute paths for read-only ancestors."""
        path = (
            self.path.relative_to(base) if self.path.is_relative_to(base) else self.path
        )
        return Item(
            file=path.as_posix(),
            line=0,
            column=0,
            kind="unsupported-file",
            message=str(replace(self, path=path)),
        )


def decode_source(path: Path, data: bytes, *, python: bool = False) -> tuple[str, str]:
    """Require lossless decoding for edits, honoring Python coding declarations."""
    bom = data.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if bom else "utf-8"
    declared = bom
    if python:
        declared = declared or any(
            re.match(
                rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)",
                line.removeprefix(b"\xef\xbb\xbf"),
            )
            for line in data.split(b"\n", 2)[:2]
        )
        try:
            encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        except SyntaxError as error:
            raise FileEncodingError(path, None, not declared) from error
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError as error:
        raise FileEncodingError(
            path,
            "UTF-8" if encoding in {"utf-8", "utf-8-sig"} else encoding,
            python and not declared,
        ) from error
