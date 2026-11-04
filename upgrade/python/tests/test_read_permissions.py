# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unreadable inputs are collected without hiding readable custom API URLs."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import main

pytestmark = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user (root can read chmod 000 paths)",
)


@pytest.mark.parametrize(
    "name", ["locked.py", "pyproject.toml", "requirements.txt", ".env", "deps"]
)
def test_reports_all_paths_when_files_cannot_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given two unreadable files, including a nonstandard requirements include.
    originals = {
        "app.py": "from daytona import Daytona\nDaytona().create()\n",
        "requirements.txt": "-r nested/a/deps\n-r nested/b/deps\ndaytona==0.224.0\n",
    }
    for relative, source in originals.items():
        (tmp_path / relative).write_text(source)
    locked = [tmp_path / "nested" / part / name for part in ("a", "b")]
    for path in locked:
        path.parent.mkdir(parents=True)
        path.write_text("# hidden input\n")
        path.chmod(0)
    try:
        # When the writing CLI scans every input.
        status = main([str(tmp_path), "--json", "--force", "--yes"])
    finally:
        for path in locked:
            path.chmod(0o600)
    # Then every unreadable file blocks all writes with a relative remedy.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "refused" not in report
    assert report["changedFiles"] == []
    assert [item for item in report["items"] if item["kind"] == "unsupported-file"] == [
        {
            "file": f"nested/{part}/{name}",
            "line": 0,
            "column": 0,
            "kind": "unsupported-file",
            "message": f"nested/{part}/{name} could not be read: Permission denied; fix its permissions, then rerun.",
        }
        for part in ("a", "b")
    ]
    for relative, source in originals.items():
        assert (tmp_path / relative).read_text() == source
    for path in locked:
        assert path.read_text() == "# hidden input\n"


@pytest.mark.parametrize("directory", [False, True])
def test_custom_url_wins_when_another_path_cannot_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], directory: bool
) -> None:
    # Given a readable custom URL alongside an unreadable file or directory.
    originals = {
        "app.py": "from daytona import Daytona\nDaytona().create()\n",
        ".env": "DAYTONA_API_URL=https://self.hosted/api\n",
    }
    for relative, source in originals.items():
        (tmp_path / relative).write_text(source)
    locked = tmp_path / ("secret" if directory else "locked.py")
    if directory:
        locked.mkdir()
    else:
        locked.write_text("# hidden input\n")
    locked.chmod(0)
    try:
        # When reading the rest of the project despite a discovery failure.
        status = main([str(tmp_path), "--json", "--force", "--yes"])
    finally:
        locked.chmod(0o700 if directory else 0o600)
    # Then the custom-URL refusal takes precedence without losing the read error.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert [item for item in report["items"] if item["kind"] == "unsupported-file"] == [
        {
            "file": locked.name,
            "line": 0,
            "column": 0,
            "kind": "unsupported-file",
            "message": f"{locked.name} could not be read: Permission denied; fix its permissions, then rerun.",
        }
    ]
    for relative, source in originals.items():
        assert (tmp_path / relative).read_text() == source


def test_reports_relative_file_when_single_file_cannot_be_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a single-file invocation with no read permission.
    path = tmp_path / "locked.py"
    source = "from daytona import Daytona\nDaytona().create()\n"
    path.write_text(source)
    path.chmod(0)
    try:
        # When
        status = main([str(path), "--json", "--force", "--yes"])
    finally:
        path.chmod(0o600)
    # Then the diagnostic is relative to the file's parent, not the absolute path.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["items"] == [
        {
            "file": "locked.py",
            "line": 0,
            "column": 0,
            "kind": "unsupported-file",
            "message": "locked.py could not be read: Permission denied; fix its permissions, then rerun.",
        }
    ]
    assert path.read_text() == source
