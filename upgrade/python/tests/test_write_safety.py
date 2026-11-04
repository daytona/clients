# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for CLI argument refusal and transactional writes."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.writes import write_outputs


def test_refuses_when_unknown_flag_is_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an otherwise valid project.
    path = tmp_path / "app.py"
    original = b"from daytona import Daytona\nd = Daytona()\nd.create()\n"
    path.write_bytes(original)
    # When an unknown flag is supplied to the real CLI.
    code = main([str(tmp_path), "--force", "--unknown-option", "--json"])
    # Then it is an unknown option, not an accepted no-op.
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["refused"]["reason"] == "bad-arguments"
    assert "--unknown-option" in report["refused"]["message"]
    assert path.read_bytes() == original


def test_restores_original_bytes_when_later_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given byte-sensitive sources and a later replacement that fails once.
    first = tmp_path / "a.py"
    last = tmp_path / "z.py"
    original = b"# coding: latin-1\r\n# caf\xe9\r\nfrom daytona import Daytona\r\nd = Daytona()\r\nd.create()\r\n"
    first.write_bytes(original)
    last.write_bytes(original)
    replace = os.replace
    failed = False

    def fail_later(source: Path, path: Path) -> None:
        nonlocal failed
        if path == last and not failed:
            failed = True
            raise PermissionError(errno.EACCES, "write denied", str(path))
        replace(source, path)

    monkeypatch.setattr(os, "replace", fail_later)
    # When the CLI applies the fully computed project changes.
    code = main([str(tmp_path), "--force", "--yes"])
    # Then earlier replacements are undone and the failing target is untouched.
    assert code == 1
    assert first.read_bytes() == original
    assert last.read_bytes() == original
    assert "z.py could not be written:" in capsys.readouterr().err


def test_removes_new_output_when_later_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a new output followed by an existing target whose write is denied.
    created = tmp_path / "new.py"
    blocked = tmp_path / "existing.py"
    original = b"original bytes\r\n"
    blocked.write_bytes(original)
    replace = os.replace

    def deny_write(source: Path, path: Path) -> None:
        if path == blocked:
            raise PermissionError(errno.EACCES, "write denied", str(path))
        replace(source, path)

    monkeypatch.setattr(os, "replace", deny_write)
    # When a later write fails after the new file has been created.
    with pytest.raises(PermissionError):
        write_outputs([(created, b"new output"), (blocked, b"blocked output")])
    # Then the newly created file is removed and the unwritable file stays intact.
    assert not created.exists()
    assert blocked.read_bytes() == original
