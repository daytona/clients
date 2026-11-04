# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Discover pytest collection settings without importing pytest."""

from __future__ import annotations

import sys
from configparser import ConfigParser, Error
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePath

import tomlkit
from tomlkit.exceptions import TOMLKitError

if sys.version_info >= (3, 11):
    import tomllib


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class PytestCollection:
    """Immutable naming rules shared by indexing and receiver analysis."""

    files: tuple[str, ...] = ("test_*.py", "*_test.py")
    classes: tuple[str, ...] = ("Test",)
    functions: tuple[str, ...] = ("test",)

    def module(self, file: str) -> bool:
        name = PurePath(file).name
        return name == "conftest.py" or any(
            fnmatch(name, pattern) for pattern in self.files
        )


def matches_name(name: str, patterns: tuple[str, ...]) -> bool:
    """Pytest accepts literal name prefixes as well as shell-style patterns."""
    return any(
        name.startswith(pattern) or fnmatch(name, pattern) for pattern in patterns
    )


def read_collection(root: Path) -> PytestCollection:
    """Search upward for the first applicable config; never merge candidates."""
    defaults = PytestCollection()
    base = root.resolve()
    candidates = (
        directory / filename
        for directory in (base, *base.parents)
        for filename in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
    )
    for path in candidates:
        if not path.is_file():
            continue
        filename = path.name
        try:
            source = path.read_text(encoding="utf-8")
            settings: dict[str, tuple[str, ...]] = {}
            if filename == "pyproject.toml":
                document = (
                    tomllib.loads(source)
                    if sys.version_info >= (3, 11)
                    else tomlkit.parse(source).unwrap()
                )
                for key in ("tool", "pytest", "ini_options"):
                    if not isinstance(document, dict) or key not in document:
                        break
                    document = document[key]
                else:
                    if isinstance(document, dict):
                        for key, value in document.items():
                            if isinstance(value, str):
                                settings[key] = tuple(value.split())
                            elif isinstance(value, list) and all(
                                isinstance(part, str) for part in value
                            ):
                                settings[key] = tuple(value)
                        return PytestCollection(
                            settings.get("python_files", defaults.files),
                            settings.get("python_classes", defaults.classes),
                            settings.get("python_functions", defaults.functions),
                        )
                continue
            parser = ConfigParser(interpolation=None)
            parser.read_string(source)
            section = "tool:pytest" if filename == "setup.cfg" else "pytest"
            if not parser.has_section(section) and filename != "pytest.ini":
                continue
            if parser.has_section(section):
                settings = {
                    key: tuple(value.split()) for key, value in parser.items(section)
                }
            return PytestCollection(
                settings.get("python_files", defaults.files),
                settings.get("python_classes", defaults.classes),
                settings.get("python_functions", defaults.functions),
            )
        except (OSError, UnicodeError, Error, ValueError, TOMLKitError):
            # Best-effort analysis must also work on unreadable/malformed projects.
            # Manifest inspection separately reports invalid pyproject.toml files.
            return defaults
    return defaults
