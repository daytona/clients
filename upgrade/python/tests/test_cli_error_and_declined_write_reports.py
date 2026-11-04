# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The CLI reports errors and previews declined writes."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from daytona_upgrade import cli
from daytona_upgrade.safety import GitSafety


@pytest.mark.parametrize("error", [OSError("read failed"), RuntimeError("bug")])
def test_json_reports_cause_when_boundary_fails(
    error: OSError | RuntimeError,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given an inspection failure at the CLI boundary.
    def fail(_root: Path) -> GitSafety:
        raise error

    monkeypatch.setattr(cli, "git_safety", fail)
    # When the JSON CLI handles the failure.
    status = cli.main([str(Path.cwd()), "--json"])
    # Then the machine-readable report carries the error, not only stderr.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["nextSteps"] == [f"daytona-upgrade: {error}"]


class TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_json_emits_preview_when_confirmation_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a real interactive input stream declining a planned source edit.
    source = "from daytona import Daytona\nclient = Daytona()\nclient.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    monkeypatch.setattr(sys, "stdin", TerminalInput("n\n"))
    # When confirmation is declined in JSON mode.
    status = cli.main([str(tmp_path), "--json"])
    # Then a dry-run report is emitted and the source remains untouched.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["dryRun"] is True
    assert report["changedFiles"] == ["app.py"]
    assert app.read_text() == source


def test_cli_upgrades_when_module_is_named_src(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a top-level module, not a src-layout directory.
    app = tmp_path / "src.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When the real CLI indexes and upgrades the project.
    status = cli.main([str(tmp_path), "--yes", "--json"])
    # Then indexing succeeds and the call is migrated.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["changedFiles"] == ["src.py"]
    assert app.read_text() == "from daytona import Daytona\nDaytona().v0.create()\n"


def test_json_explains_failure_when_notebook_is_malformed(tmp_path: Path) -> None:
    # Given a real invalid notebook, without replacing any CLI collaborator.
    (tmp_path / "bad.ipynb").write_text('{"cells":42}')
    # When the installed module handles the input through its process entrypoint.
    result = subprocess.run(
        [sys.executable, "-m", "daytona_upgrade", str(tmp_path), "--json", "--dry"],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then JSON consumers can diagnose the nonzero exit without reading stderr.
    assert result.returncode == 1
    assert json.loads(result.stdout)["items"] == [
        {
            "file": "bad.ipynb",
            "line": 0,
            "column": 0,
            "kind": "unsupported-file",
            "message": "bad.ipynb is not a valid notebook: Notebook must contain a cells array; fix or remove it, then rerun.",
        }
    ]
