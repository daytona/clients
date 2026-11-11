# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic discovery with lossless edit checks deferred until preflight."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass, replace
from pathlib import Path

from ._requirements import SKIP, requirement_paths
from .compose_detection import compose_candidate, is_compose
from .compose_discovery import compose_references
from .compose_includes import include_entries
from .compose_projects import ComposeContext, load_compose_projects
from .environment import EnvironmentWrite
from .models import Item
from .project_paths import (
    PYTHON_SOURCE_SUFFIXES,
    environment_base,
    is_environment,
    is_manifest,
)
from .regular_files import DISCOVERY_LIMIT, INPUT_LIMIT, read_regular_bytes
from .source_encoding import FileEncodingError, decode_source


@dataclass(frozen=True)
class ProjectFile:
    path: Path
    relative: str
    source: str
    encoding: str
    requirements: bool = False
    constraint: bool = False
    encoding_error: FileEncodingError | None = None
    compose: bool = False
    compose_directories: tuple[Path, ...] = ()


def module_name(relative: str) -> str:
    """Normalize discovered Python paths into project import identities."""
    parts = list(Path(relative).with_suffix("").parts)
    if len(parts) > 1 and parts[0] == "src":
        parts.pop(0)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def discover(root: Path, items: list[Item]) -> tuple[ProjectFile, ...]:
    """Ignore generated trees and symlinks; accumulate traversal failures in items."""
    base = root.parent if root.is_file() else root

    def onerror(error: OSError) -> None:
        relative = Path(os.path.relpath(error.filename or root, base)).as_posix()
        items.append(
            Item(
                file=relative,
                line=0,
                column=0,
                kind="unsupported-file",
                message=f"{relative} could not be read: {error.strerror}; "
                + (
                    "fix its permissions, then rerun."
                    if error.errno in (errno.EACCES, errno.EPERM)
                    else "fix the problem, then rerun."
                ),
            )
        )

    paths: list[Path] = []
    if root.is_file():
        paths.append(root)
    else:
        for directory, dirs, files in os.walk(root, onerror=onerror):
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in SKIP and not (Path(directory) / name).is_symlink()
            )
            paths.extend(Path(directory) / name for name in sorted(files))
    environment_root = environment_base(base)
    requirements = requirement_paths(base, paths)
    compose = compose_references(paths, base, items)
    paths = sorted(set(paths) | requirements.paths | compose)
    discovered: list[ProjectFile] = []
    for path in paths:
        if (path.is_symlink() and path not in compose) or not (
            path in compose
            or path.suffix in PYTHON_SOURCE_SUFFIXES
            or path.suffix in {".ipynb", ".yaml", ".yml"}
            or path in requirements.paths
            or is_manifest(path)
            or is_environment(Path(os.path.relpath(path.resolve(), environment_root)))
            or path.name == "package.json"
        ):
            continue
        try:
            data = read_regular_bytes(path, DISCOVERY_LIMIT)
        except OSError as error:
            if path in compose:
                items.extend(
                    EnvironmentWrite(
                        Path(os.path.relpath(path, base)).as_posix(), 1, 1, None
                    ).items(False)
                )
            else:
                onerror(error)
            continue
        encoding_error = None
        try:
            source, encoding = decode_source(
                path,
                data,
                python=path.suffix in PYTHON_SOURCE_SUFFIXES
                and path not in requirements.paths,
            )
        except FileEncodingError as error:
            encoding_error = error
            source, encoding = data.decode("utf-8-sig", errors="replace"), "utf-8-sig"
            if path in compose:
                items.extend(
                    EnvironmentWrite(
                        Path(os.path.relpath(path, base)).as_posix(), 1, 1, None
                    ).items(False)
                )
        yaml = path.suffix in {".yaml", ".yml"}
        candidate = path in compose or yaml and compose_candidate(path, source)
        environment = is_environment(
            Path(os.path.relpath(path.resolve(), environment_root))
        )
        if len(data) > INPUT_LIMIT and (candidate or environment):
            items.extend(
                EnvironmentWrite(
                    Path(os.path.relpath(path, base)).as_posix(), 1, 1, None
                ).items(False)
            )
            continue
        if yaml and not candidate and not environment:
            continue
        discovered.append(
            ProjectFile(
                path,
                Path(os.path.relpath(path, base)).as_posix(),
                source,
                encoding,
                path in requirements.paths,
                path in requirements.constraints,
                encoding_error,
                path in compose or yaml and is_compose(path, source),
            )
        )
    # Expand before preflight computes shared project bases or scans any file.
    # Included YAML and custom-named manifests receive identical Compose rules.
    projects = {file.path: file for file in discovered}
    directories = tuple(
        sorted({file.path.parent for file in discovered if file.compose})
    )
    for file in discovered:
        if not file.compose or not tuple(include_entries(file.source)):
            continue
        inputs = load_compose_projects(
            file.source, ComposeContext(base, file.relative, directories)
        )
        items.extend(inputs.items)
        projects[file.path] = replace(file, compose_directories=inputs.directories)
        for project in inputs.projects[1:]:
            path = (base / project.file).absolute()
            if path in projects:
                projects[path] = replace(projects[path], compose=True)
            else:
                projects[path] = ProjectFile(
                    path, project.file, project.source, "utf-8-sig", compose=True
                )
    return tuple(projects[path] for path in sorted(projects))
