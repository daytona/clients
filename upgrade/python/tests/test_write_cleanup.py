# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Rollback diagnostics survive failures while cleaning staged outputs."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from daytona_upgrade.writes import write_outputs


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_write_error_survives_when_temporary_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rollback_fails: bool
) -> None:
    # Given a failed second replacement, with selective temporary unlink failures.
    paths = [tmp_path / f"{name}.py" for name in ("a", "b", "c")]
    for path in paths:
        path.write_bytes(b"original")
    replace = os.replace
    unlink = Path.unlink
    calls = 0
    attempted: list[Path] = []
    leftovers: list[Path] = []

    def fail_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2 or (rollback_fails and calls > 2):
            raise PermissionError(errno.EACCES, "replace denied", str(destination))
        replace(source, destination)

    def fail_unlink(path: Path, missing_ok: bool = False) -> None:
        attempted.append(path)
        if path.name.startswith((".a.py.", ".b.py.")) and path.exists():
            leftovers.append(path)
            raise PermissionError(errno.EACCES, "unlink denied", str(path))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(os, "replace", fail_replace)
    # Python 3.9's pathlib accessor caches os.unlink, so patch the cleanup seam.
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    # When committing outputs fails, including cleanup of some staged files.
    with pytest.raises(OSError) as caught:
        write_outputs([(path, b"updated") for path in paths], tmp_path)
    # Then the logical write error survives with rollback and all leftover paths.
    message = str(caught.value)
    assert caught.value.filename == "b.py"
    assert "replace denied" in message
    assert ("rollback failed for: a.py:" in message) is rollback_fails
    assert "leftover temp files:" in message
    assert leftovers
    assert all(path.relative_to(tmp_path).as_posix() in message for path in leftovers)
    assert str(tmp_path) not in message
    assert any(path.name.startswith(".c.py.") for path in attempted)
    assert sorted(tmp_path.glob("*.daytona-upgrade.tmp")) == sorted(leftovers)
