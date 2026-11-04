# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Git inspection is rooted in the target, unless explicitly bypassed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.safety import git_safety

GIT_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CEILING_DIRECTORIES",
)


def test_dirty_target_when_environment_redirects_to_clean_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given two real repositories, only the target containing untracked source.
    target, decoy = tmp_path / "target", tmp_path / "decoy"
    for directory in (target, decoy):
        subprocess.run(["git", "init", str(directory)], check=True, capture_output=True)
    (target / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    # When inspecting the target rather than the inherited repository.
    safety = git_safety(target)
    # Then the untracked source still blocks a writing migration.
    assert safety.dirty


def test_git_invocation_when_repository_variables_are_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a wire executable that rejects redirection or a missing -C root.
    binary = tmp_path / "git"
    conditions = " || ".join(f'[ -n "${{{name}+x}}" ]' for name in GIT_VARIABLES)
    binary.write_text(
        f"#!/bin/sh\nif {conditions}; then exit 99; fi\n"
        f'[ "$1" = -C ] && [ "$2" = "{tmp_path}" ] || exit 98\n'
        'if [ "$3" = status ]; then echo "?? app.py"; else echo true; fi\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    for name in GIT_VARIABLES:
        monkeypatch.setenv(name, "redirect")
    # When both discovery and status cross the subprocess boundary.
    safety = git_safety(tmp_path)
    # Then every repository-affecting override has been removed.
    assert safety.dirty


def test_cli_writes_when_force_bypasses_broken_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a Git executable that records and fails any invocation.
    marker = tmp_path / "git-called"
    binary = tmp_path / "git"
    binary.write_text(f'#!/bin/sh\necho called > "{marker}"\nexit 99\n')
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    source = tmp_path / "app.py"
    source.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When the user explicitly bypasses Git inspection.
    status = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then migration succeeds without executing Git at all.
    assert status == 0
    assert not marker.exists()
    assert "Daytona().v0.create()" in source.read_text()
