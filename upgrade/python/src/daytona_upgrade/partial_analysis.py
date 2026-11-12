# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only detection of project context outside a selected scan root."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from ._requirements import SKIP
from .manifest_errors import ManifestParseError
from .manifests import inspect_manifest
from .models import Item, load_mapping
from .project_paths import PYTHON_SOURCE_SUFFIXES, is_manifest


def partial_analysis(root: Path) -> tuple[Item, ...]:
    """Report once when parent manifests or checkout sources escape the scan."""
    base = root.parent if root.is_file() else root
    boundary = next(
        (parent for parent in (base, *base.parents) if (parent / ".git").exists()), None
    )
    parents: list[Path] = []
    for depth, parent in enumerate((base, *base.parents)):
        if parent == root:
            continue
        if boundary is not None and not parent.is_relative_to(boundary):
            break
        if boundary is None and depth > 5:
            break
        parents.append(parent)
    partial = False
    mapping = load_mapping()
    for parent in parents:
        try:
            manifests = [
                path
                for path in parent.iterdir()
                if is_manifest(path) and path.is_file() and not path.is_symlink()
            ]
        except OSError:
            continue  # Ancestor context is read-only, not part of owned discovery.
        for path in manifests:
            try:
                manifest = inspect_manifest(
                    path, path.read_text(encoding="utf-8"), mapping
                )
            except (OSError, UnicodeError, ManifestParseError):
                continue  # Unreadable outside context is not dependency evidence.
            if manifest.found:
                partial = True
                break
        if partial:
            break
    if not partial and boundary is not None and boundary != root:
        for directory, dirs, files in os.walk(boundary):
            parent = Path(directory)
            dirs[:] = [
                name
                for name in dirs
                if name not in SKIP
                and not (parent / name).is_symlink()
                and parent / name != root
            ]
            for name in files:
                path = parent / name
                if (
                    path == root
                    or path.suffix not in PYTHON_SOURCE_SUFFIXES
                    or path.is_symlink()
                ):
                    continue
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue  # Outside the owned scan; no edits or completeness claim.
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    # Older interpreters may not understand an omitted sibling's syntax.
                    if re.search(
                        r"\b(?:import|from)\s+(?:daytona|daytona_sdk)\b", source
                    ):
                        partial = True
                        break
                    continue
                if any(
                    isinstance(node, ast.Import)
                    and any(
                        alias.name.split(".")[0] in {"daytona", "daytona_sdk"}
                        for alias in node.names
                    )
                    or isinstance(node, ast.ImportFrom)
                    and node.level == 0
                    and (node.module or "").split(".")[0] in {"daytona", "daytona_sdk"}
                    for node in ast.walk(tree)
                ):
                    partial = True
                    break
            if partial:
                break
    return (
        (
            Item(
                file=".",
                line=0,
                column=0,
                kind="types-unresolved",
                message="Analysis is partial: Python packages or dependency manifests exist outside the scan root. Scan the full project to include shared clients.",
            ),
        )
        if partial
        else ()
    )
