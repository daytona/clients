# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only deployment environment preflight for discovered project files."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .compose_environment import ComposeContext, scan_compose_environment
from .environment import scan_environment
from .models import Item, TransformOptions
from .project_discovery import ProjectFile
from .project_paths import is_environment


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class ProjectEnvironment:
    """Keep scan roots and Compose candidate bases constant across the project."""

    root: Path
    environment_root: Path
    compose_directories: tuple[Path, ...]

    def scan(self, file: ProjectFile, options: TransformOptions) -> tuple[Item, ...]:
        """Inspect inline setters and every declared Compose env_file candidate."""
        if not (
            file.compose
            or is_environment(file.path.resolve().relative_to(self.environment_root))
        ):
            return ()
        # A neutral YAML basename also bypasses Dockerfile's basename dispatch.
        environment_files = (
            ("compose.yaml", file.relative)
            if file.compose and file.path.name.startswith("Dockerfile")
            else ("compose.yaml",)
            if file.compose
            else (file.relative,)
        )
        items = [
            item
            for environment_file in environment_files
            for write in scan_environment(file.source, environment_file)
            for item in replace(write, file=file.relative).items(options.drop_api_url)
        ]
        if file.compose:
            items.extend(
                scan_compose_environment(
                    file.source,
                    options,
                    ComposeContext(self.root, file.relative, self.compose_directories),
                )
            )
        return tuple(items)
