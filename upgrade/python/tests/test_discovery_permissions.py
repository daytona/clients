# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Traversal failures block migration rather than hiding inaccessible source."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user (root can traverse chmod 000 directories)",
)
@pytest.mark.parametrize("migratable", [False, True])
def test_refuses_when_scanned_directories_cannot_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], migratable: bool
) -> None:
    # Given two unreadable subtrees, possibly alongside migratable source.
    app = tmp_path / "app.py"
    source = (
        "from daytona import Daytona\nDaytona().create()\n"
        if migratable
        else "value = 1\n"
    )
    app.write_text(source)
    directories = [tmp_path / "nested" / name for name in ("a", "b")]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "hidden.py").write_text("from daytona import Daytona\n")
        directory.chmod(0)
    try:
        # When the writing CLI encounters traversal errors.
        status = main([str(tmp_path), "--json", "--force", "--yes"])
    finally:
        for directory in directories:
            directory.chmod(0o700)
    # Then every inaccessible path is actionable, with no partial writes or refusal.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "refused" not in report
    assert report["changedFiles"] == []
    assert [
        (item["file"], item["kind"], item["message"]) for item in report["items"]
    ] == [
        (
            f"nested/{name}",
            "unsupported-file",
            f"nested/{name} could not be read: Permission denied; fix its permissions, then rerun.",
        )
        for name in ("a", "b")
    ]
    assert app.read_text() == source


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user (root can traverse chmod 000 directories)",
)
def test_migrates_when_unreadable_directory_is_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an unreadable generated tree outside the scan's scope.
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    skipped = tmp_path / ".venv"
    skipped.mkdir()
    skipped.chmod(0)
    try:
        # When migrating the project without descending into skipped trees.
        status = main([str(tmp_path), "--json", "--force", "--yes"])
    finally:
        skipped.chmod(0o700)
    # Then the inaccessible generated tree does not prevent migration.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert not any(item["kind"] == "unsupported-file" for item in report["items"])
    assert "Daytona().v0.create()" in app.read_text()


def test_custom_url_refusal_takes_precedence_over_unsupported_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a custom API URL next to a file that cannot be parsed.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / ".env").write_text("DAYTONA_API_URL=https://self.hosted/api\n")
    (tmp_path / "app.py").write_text("from daytona import Daytona\nDaytona().create(\n")
    # When
    status = main([str(tmp_path), "--dry", "--json", "--force"])
    # Then the documented refusal wins and still lists the unsupported file.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert any(item["kind"] == "unsupported-file" for item in report["items"])
