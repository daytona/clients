# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only, cycle-guarded traversal of literal Compose include projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .compose_includes import include_entries
from .environment import EnvironmentWrite
from .models import Item
from .regular_files import read_environment_text


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class ComposeContext:
    """The declaring manifest plus every plausible Compose project directory."""

    root: Path | None
    file: str
    directories: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ComposeProject:
    """A manifest scanned as Compose regardless of filename or location."""

    source: str
    file: str


@dataclass(frozen=True)
class ComposeInputs:
    """Reachable manifests, candidate project directories, and loader reviews."""

    projects: tuple[ComposeProject, ...]
    directories: tuple[Path, ...]
    items: tuple[Item, ...]


def load_compose_projects(source: str, context: ComposeContext) -> ComposeInputs:
    """Visit lexical locations with real-path cycle guards; scan every candidate."""
    root = context.root
    ancestors = frozenset({(root / context.file).resolve()}) if root else frozenset()
    pending = [(source, context.file, ancestors)]
    projects: list[ComposeProject] = []
    directories = set(context.directories)
    seen = {Path(os.path.abspath(root / context.file))} if root else set()
    items: list[Item] = []
    while pending:
        loaded, file, ancestors = pending.pop()
        includes = tuple(include_entries(loaded))
        projects.append(ComposeProject(loaded, file))
        bases = (
            {
                root,
                (root / file).parent,
                (root / file).resolve().parent,
                *context.directories,
            }
            if root is not None
            else set()
        )
        for include in includes:
            review = EnvironmentWrite(file, 1, 1, None).items(False)
            if include.uncertain or not include.paths:
                items.extend(review)
            if include.project_directory is not None:
                directories.update(base / include.project_directory for base in bases)
            for literal in include.paths:
                if literal is None or root is None:
                    items.extend(review)
                    continue
                found = False
                for path in sorted({base / literal for base in bases}):
                    real = path.resolve()
                    parents = {path.parent, real.parent}
                    if include.project_directory is not None:
                        directories.update(
                            parent / include.project_directory for parent in parents
                        )
                    lexical = Path(os.path.abspath(path))
                    if real in ancestors or lexical in seen:
                        found = True
                        directories.update(parents)
                        continue
                    try:
                        included_source = read_environment_text(path)
                    except FileNotFoundError:
                        continue
                    except (OSError, UnicodeError):
                        items.extend(review)
                        continue
                    seen.add(lexical)
                    found = True
                    directories.update(parents)
                    pending.append(
                        (
                            included_source,
                            Path(os.path.relpath(lexical, root)).as_posix(),
                            ancestors | {real},
                        )
                    )
                if not found:
                    items.extend(review)
    return ComposeInputs(tuple(projects), tuple(sorted(directories)), tuple(items))
