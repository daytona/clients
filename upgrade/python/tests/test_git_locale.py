# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Git safety must work under a translated caller environment."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import _dirty


def test_recognizes_non_git_directory_when_caller_locale_is_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a git wire fake that translates its error unless explicitly localized.
    binary = tmp_path / "git"
    binary.write_text(
        '#!/bin/sh\nif [ "$LC_ALL" = C ] && [ "$LANG" = C ]; then\n echo "fatal: not a git repository" >&2\nelse\n echo "fatal: kein Git-Repository" >&2\nfi\nexit 128\n'
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    # When the real subprocess boundary inspects a non-git project.
    dirty = _dirty(tmp_path)
    # Then the directory is accepted rather than an internal inspection error.
    assert dirty is False
