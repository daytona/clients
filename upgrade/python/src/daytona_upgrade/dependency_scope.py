# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only ancestor dependency recognition for partial-root upgrades."""

from __future__ import annotations

import os
from pathlib import Path

from ._requirements import _includes
from .manifest_errors import ManifestParseError
from .manifests import inspect_manifest
from .models import Item, Mapping


def inspect_parent_dependencies(
    root: Path, mapping: Mapping, report_root: Path
) -> tuple[bool, tuple[Item, ...]]:
    """Inspect owner ancestors, reporting in scan-root coordinates without edits."""
    items: list[Item] = []
    base = root.resolve().parent if root.is_file() else root.resolve()
    directories = (base, *base.parents)
    boundary = next(
        (directory for directory in directories if (directory / ".git").exists()), base
    )
    nearest_proof: bool | None = None
    for directory in directories:
        found = False
        certified: dict[Path, bool] = {}
        constraints: set[Path] = set()
        pending = [
            (directory / name, name.endswith((".txt", ".in")), False)
            for name in (
                "pyproject.toml",
                "setup.cfg",
                "setup.py",
                "Pipfile",
                "requirements.txt",
                "requirements.in",
            )
        ]
        visited: set[tuple[Path, bool, bool]] = set()
        while pending:
            path, requirement, constrained = pending.pop()
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
                continue
            path = path.resolve()
            if (
                (path, requirement, constrained) in visited
                or not path.is_relative_to(boundary)
                or not path.is_file()
            ):
                continue
            visited.add((path, requirement, constrained))
            if constrained:
                constraints.add(path)
            try:
                source = path.read_text(encoding="utf-8")
                manifest = inspect_manifest(
                    Path("requirements.txt") if requirement else path,
                    source,
                    mapping,
                )
            except (
                OSError,
                ManifestParseError,
                UnicodeDecodeError,
            ) as error:
                relative = Path(os.path.relpath(path, report_root)).as_posix()
                message = (
                    str(ManifestParseError(Path(relative), error.reason, error.format))
                    + " Verify the consuming runtime dependency manually."
                    if isinstance(error, ManifestParseError)
                    else f"Skipped unreadable ancestor manifest {relative}: {error.strerror if isinstance(error, OSError) else error}; verify the consuming runtime dependency manually."
                )
                items.append(
                    Item(
                        file=relative,
                        line=0,
                        column=0,
                        kind="manual-dependency",
                        message=message,
                    )
                )
                continue
            items.extend(
                Item(
                    file=Path(os.path.relpath(path, report_root)).as_posix(),
                    line=line,
                    column=1 if line else 0,
                    kind="manual-dependency",
                    message=message,
                )
                for line, message in (
                    *(
                        (line, message)
                        for line, message in manifest.manual
                        if message.startswith("Review tool.uv.sources overrides for ")
                    ),
                    *manifest.resolver_review,
                )
            )
            found |= manifest.found and not constrained
            if manifest.found and not constrained:
                certified[path] = manifest.v2_found
            if requirement:
                for include, constraint in _includes(source):
                    if "://" in include:
                        continue
                    included = path.parent / include
                    pending.append((included, True, constrained or constraint))
        if found and nearest_proof is None:
            # Freeze the nearest owner's proof, but keep checking ancestor overrides.
            nearest_proof = all(
                proof and path not in constraints for path, proof in certified.items()
            )
        if directory == boundary:
            break
    return nearest_proof is True, tuple(items)
