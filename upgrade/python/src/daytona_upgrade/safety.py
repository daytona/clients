# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only Git safety inspection, including environments without Git."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

NON_REPOSITORY: Final = "Not a Git repository; review the changes and keep a backup."
_REPOSITORY_ENV: Final = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CEILING_DIRECTORIES",
    }
)


@dataclass(frozen=True)
class GitSafety:
    """A dirty tree refuses writes; an unavailable repository requires a note."""

    dirty: bool = False
    note: str = ""


def git_safety(root: Path) -> GitSafety:
    """Inspect Git without changing the tree or requiring Git to be installed."""
    directory = root.parent if root.is_file() else root
    env = {
        key: value for key, value in os.environ.items() if key not in _REPOSITORY_ENV
    }
    env.update(LC_ALL="C", LANG="C")
    try:
        check = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return GitSafety(
            note="Git is not installed; review the changes and keep a backup."
        )
    if check.returncode:
        if "not a git repository" in check.stderr:
            return GitSafety(note=NON_REPOSITORY)
        raise OSError(check.stderr.strip() or "Unable to inspect Git working tree")
    status = subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "status",
            "--porcelain",
            "--untracked-files=normal",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if status.returncode:
        raise OSError(status.stderr.strip())
    return GitSafety(dirty=bool(status.stdout))


def _dirty(root: Path) -> bool:
    """Retain the existing boolean inspection helper for callers."""
    return git_safety(root).dirty
