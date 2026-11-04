# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal COMPOSE_FILE references from project dotenv inputs, without execution."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .compose_yaml import literal_path
from .environment import EnvironmentWrite
from .models import Item
from .regular_files import read_environment_text
from .yaml_flow import _without_comment


def compose_references(paths: list[Path], base: Path, items: list[Item]) -> set[Path]:
    """Include explicitly selected manifests regardless of their file extension."""
    references: set[Path] = set()
    for path in paths:
        if path.name != ".env" or path.is_symlink():
            continue
        try:
            source = read_environment_text(path)
        except (OSError, UnicodeError):
            continue  # Ordinary discovery reports unreadable dotenv inputs.
        settings: dict[str, tuple[int, str | None]] = {}
        for number, line in enumerate(source.splitlines(), 1):
            field = re.fullmatch(
                r"\s*(?:export\s+)?(COMPOSE_FILE|COMPOSE_PATH_SEPARATOR)\s*=\s*(.*?)\s*",
                _without_comment(line),
            )
            if field is None:
                continue
            value = field[2]
            literal = (
                literal_path(value)
                if value.startswith(("'", '"'))
                else value
                if re.fullmatch(r"[^\s'\"$`]+", value)
                else None
            )
            if literal is not None and any(
                marker in literal for marker in ("$", "{{", "{%")
            ):
                literal = None
            settings[field[1]] = number, literal
        if "COMPOSE_FILE" not in settings:
            continue
        number, manifests = settings["COMPOSE_FILE"]
        separator = settings.get("COMPOSE_PATH_SEPARATOR", (0, os.pathsep))[1]
        if manifests is None or not separator:
            items.extend(
                EnvironmentWrite(
                    Path(os.path.relpath(path, base)).as_posix(), number, 1, None
                ).items(False)
            )
            continue
        references.update(
            (directory / filename).absolute()
            for filename in manifests.split(separator)
            if filename
            for directory in {path.parent, base}
        )
    return references
