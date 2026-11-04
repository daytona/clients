# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Logical pip requirement records and root-contained local include discovery."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SKIP: Final = frozenset(
    {
        ".venv",
        ".tox",
        "venv",
        "site-packages",
        "node_modules",
        ".git",
        "build",
        "dist",
        "__pycache__",
    }
)


def is_requirements(path: Path) -> bool:
    return (
        path.suffix == ".in" and path.name.startswith(("requirements", "constraints"))
    ) or (
        path.suffix == ".txt"
        and (
            path.name.startswith(("requirements", "constraints"))
            or path.name.endswith("-requirements.txt")
        )
    )


def records(source: str) -> Iterator[tuple[int, str]]:
    """Yield original logical records with their first physical line number."""
    pending: list[str] = []
    start = 1
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        if not pending:
            start = number
        pending.append(line)
        if not line.rstrip("\r\n").endswith("\\"):
            yield start, "".join(pending)
            pending.clear()
    if pending:
        yield start, "".join(pending)


def _includes(source: str) -> Iterator[tuple[str, bool]]:
    for _, record in records(source):
        logical = re.sub(r"\\\r?\n", "", record).lstrip("\ufeff").strip()
        if not logical.startswith(("-r", "-c", "--requirement", "--constraint")):
            continue
        try:
            parts = shlex.split(logical, comments=True)
        except ValueError:
            continue  # Malformed include syntax is not a local path.
        if not parts:
            continue
        if parts[0] in {"-r", "-c", "--requirement", "--constraint"}:
            if len(parts) == 2:
                yield parts[1], parts[0] in {"-c", "--constraint"}
        elif len(parts) == 1:
            match = re.fullmatch(
                r"(?:-r|-c|--requirement=|--constraint=)(.+)", parts[0]
            )
            if match:
                yield match[1], parts[0].startswith(("-c", "--constraint="))


@dataclass(frozen=True)
class RequirementFiles:
    """Discovered paths retain constraint semantics, which prohibit extras."""

    paths: frozenset[Path]
    constraints: frozenset[Path]


def requirement_paths(root: Path, paths: list[Path]) -> RequirementFiles:
    """Follow local includes once, refusing symlinks, root escapes and generated trees."""
    base = root.absolute()
    pending = [
        (path, path.suffix == ".txt" and path.name.startswith("constraints"))
        for path in paths
        if is_requirements(path)
    ]
    visited: set[tuple[Path, bool]] = set()
    while pending:
        candidate, constraint = pending.pop()
        if candidate.is_symlink() or any(
            parent.is_symlink() for parent in candidate.parents
        ):
            continue
        path = Path(os.path.abspath(candidate))
        if (path, constraint) in visited:
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if set(relative.parts) & SKIP or not path.is_file():
            continue
        visited.add((path, constraint))
        # Includes are scanned only; preflight checks losslessness for planned edits.
        try:
            source = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            # Keep the visited path: discovery reports its read failure with the
            # other inputs, rather than stopping at the first unreadable include.
            continue
        for include, nested_constraint in _includes(source):
            if "://" not in include:
                pending.append((path.parent / include, constraint or nested_constraint))
    return RequirementFiles(
        frozenset(path for path, _ in visited),
        frozenset(path for path, constraint in visited if constraint),
    )
