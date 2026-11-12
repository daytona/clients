# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Immutable project preflight reports."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .models import Item
from .notebooks import read_notebook
from .project_discovery import ProjectFile
from .project_paths import LocalModule


@dataclass(frozen=True)
class Change:
    file: ProjectFile
    source: str

    def changes_runtime(self) -> bool:
        """Comment-only guidance does not introduce a new dependency requirement."""
        before, after = self.file.source, self.source
        if self.file.path.suffix == ".ipynb":
            before, after = (
                read_notebook(before).combined,
                read_notebook(after).combined,
            )
        return ast.dump(ast.parse(before)) != ast.dump(ast.parse(after))


@dataclass(frozen=True)
class Dependency:
    file: str
    before: str
    after: str


@dataclass(frozen=True)
class ProjectResult:
    changes: tuple[Change, ...]
    dependencies: tuple[Dependency, ...]
    items: tuple[Item, ...]
    found: bool
    local_module: LocalModule | None = None
    # Each Daytona declaration's manifest, package name and constraint ("" for none).
    declared: tuple[tuple[Path, str, str], ...] = ()
