# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Preserve setup.cfg formatting while respecting setuptools list-semi rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from packaging.requirements import InvalidRequirement, Requirement

from ._dependencies import Migration
from .python_floor import PYTHON_REVIEW, needs_python_review

_SECTION: Final = re.compile(r"^\s*\[([^]]+)\]")
_OPTION: Final = re.compile(r"^(\s*)([\w-]+)(\s*[=:]\s*)(.*)$")


def _requirements(
    value: str, migration: Migration, multiline: bool, base_dir: Path
) -> str:
    """Newlines disable semicolon splitting, allowing dangling PEP 508 markers."""
    if not value.strip() or value.lstrip().startswith(("#", ";")):
        return value
    file_reference = re.match(r"^\s*file\s*:\s*(.*)", value, re.IGNORECASE)
    if file_reference:
        for filename in file_reference[1].split(","):
            path = base_dir / filename.strip().strip("'\"")
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            for line in source.splitlines():
                requirement = re.split(r"\s+#", line, maxsplit=1)[0].strip()
                if not requirement or requirement.startswith("#"):
                    continue
                try:
                    parsed = Requirement(requirement)
                except InvalidRequirement:
                    if "daytona" in source.lower():
                        migration.unparsed_requirement()
                        return value
                else:
                    if re.sub(r"[-_.]+", "-", parsed.name).lower() in migration.names:
                        migration.unparsed_requirement()
                        return value
        return value
    parts = [value] if multiline else value.split(";")
    try:
        for part in parts:
            requirement = re.split(r"\s+#", part, maxsplit=1)[0].strip()
            if requirement:
                Requirement(requirement)
    except InvalidRequirement:
        names = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)
        if any(
            re.sub(r"[-_.]+", "-", name).lower() in migration.names for name in names
        ):
            migration.unparsed_requirement()
        return value
    return ";".join(migration.requirement(part) for part in parts)


def setup_cfg(source: str, migration: Migration, base_dir: Path) -> str:
    """Edit dependency options without reserializing the INI document."""
    section = ""
    active = False
    multiline = False
    python_active = False
    python_constraint: list[str] = []
    indentation = 0
    result: list[str] = []
    lines = source.splitlines(keepends=True)
    for number, line in enumerate(lines, 1):
        migration.line = number
        header = _SECTION.match(line)
        if header:
            section, active = header[1].strip().lower(), False
            python_active = False
        if python_active and len(line) - len(line.lstrip()) > indentation:
            python_constraint.append(line.strip())
            result.append(line)
            continue
        if active and line.strip() and len(line) - len(line.lstrip()) > indentation:
            result.append(_requirements(line, migration, multiline, base_dir))
            continue
        option = _OPTION.match(line.rstrip("\r\n"))
        if option and not line.lstrip().startswith(("#", ";")):
            python_active = (
                section == "options" and option[2].lower() == "python_requires"
            )
            if python_active:
                indentation = len(option[1])
                python_constraint.append(option[4].strip())
            migration.certify = section == "options"
            active = (
                section == "options.extras_require"
                or migration.certify
                and option[2].lower() == "install_requires"
            )
            if active:
                indentation = len(option[1])
                following = next(
                    (
                        text
                        for text in lines[number:]
                        if text.strip() and not text.lstrip().startswith(("#", ";"))
                    ),
                    "",
                )
                multiline = len(following) - len(following.lstrip()) > indentation
                ending = line[len(line.rstrip("\r\n")) :]
                line = (
                    "".join(option.groups()[:3])
                    + _requirements(option[4], migration, multiline, base_dir)
                    + ending
                )
        elif active and line[:1].isspace():
            line = _requirements(line, migration, multiline, base_dir)
        elif line.strip() and not line.lstrip().startswith(("#", ";")):
            active = False
        result.append(line)
    if python_constraint and needs_python_review("".join(python_constraint)):
        migration.manual.append((0, PYTHON_REVIEW))
    return "".join(result)
