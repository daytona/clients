# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only reporting bypasses Git cleanliness without weakening write safety."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "flags",
    [
        ["--suggest-v2", "--json"],
        ["--dry", "--json"],
        ["--print", "--json"],
        ["--json"],
    ],
)
def test_cli_allows_dirty_tree_when_mode_is_readonly(
    tmp_path: Path, flags: list[str]
) -> None:
    # Given a real Git repository with untracked SDK source and dependency files.
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    originals = {
        "app.py": b"from daytona import Daytona\nDaytona().create()\n",
        "requirements.txt": b"daytona==0.224.0\n",
    }
    for name, payload in originals.items():
        (tmp_path / name).write_bytes(payload)
    # When invoking the process entrypoint without --force.
    result = subprocess.run(
        [sys.executable, "-m", "daytona_upgrade", str(tmp_path), *flags],
        text=True,
        capture_output=True,
        check=False,
    )
    # Then only writing mode refuses; read-only modes report without changing files.
    report = json.loads(result.stdout)
    readonly = flags != ["--json"]
    assert result.returncode == (0 if readonly else 2), result.stderr
    assert report["dryRun"] is readonly
    if readonly:
        assert "refused" not in report
        assert report["changedFiles"]
    else:
        assert report["refused"]["reason"] == "dirty-tree"
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals
