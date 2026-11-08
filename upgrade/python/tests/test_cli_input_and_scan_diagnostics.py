# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The project CLI reports environment, input, scan and write diagnostics."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.environment import scan_environment


@pytest.mark.parametrize("name", ["DAYTONA_API_URL", "DAYTONA_SERVER_URL"])
def test_reports_environment_when_v0_ignores_setting(name: str) -> None:
    # Given an active Cloud endpoint setting.
    source = f"{name}=https://app.daytona.io/api\n"
    # When scanning its SDK compatibility.
    items = scan_environment(source, ".env")[0].items(False)
    # Then guidance distinguishes the Cloud-only v0 namespace from v2.
    assert (
        items[0]["message"]
        == f"{name} is ignored by SDK 2.x: .v0 targets Daytona Cloud, and .v2 reads only DAYTONA_V2_API_URL, for test environments. Review this setting."
    )


@pytest.mark.parametrize("source", ['[project]\nname="a"\nname="b"\n', "[project\n"])
def test_reports_file_when_manifest_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    # Given invalid TOML and a source that would otherwise migrate.
    (tmp_path / "pyproject.toml").write_text(source)
    app = tmp_path / "app.py"
    original = "from daytona import Daytona\nDaytona().create()\n"
    app.write_text(original)
    # When preflighting the project.
    status = main([str(tmp_path), "--force", "--json"])
    # Then a structured file-specific error replaces the traceback and blocks writes.
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert status == 1
    assert captured.err == ""
    assert [(item["file"], item["kind"]) for item in report["items"]] == [
        ("pyproject.toml", "unsupported-file")
    ]
    assert "pyproject.toml is not valid TOML:" in report["items"][0]["message"]
    assert app.read_text() == original


@pytest.mark.parametrize(
    "name",
    [
        "dev-requirements.txt",
        "requirements-dev.txt",
        "test-requirements.txt",
        "constraints.txt",
        "constraints-ci.txt",
    ],
)
def test_migrates_dependency_when_alternate_requirement_file_is_standalone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given a standalone conventional requirements or constraints file.
    path = tmp_path / name
    path.write_text("daytona<1\n")
    # When upgrading the project.
    status = main([str(tmp_path), "--force", "--json"])
    # Then it is discovered and constraints cannot silently enable extras.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert "daytona" in path.read_text() and ">=2,<3" in path.read_text()
    assert ("[v0-sdk]" not in path.read_text()) == name.startswith("constraints")
    assert bool(report["items"]) == name.startswith("constraints")


@pytest.mark.parametrize("parent_manifest", [True, False])
def test_reports_partial_analysis_when_project_extends_above_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], parent_manifest: bool
) -> None:
    # Given a service nested in a checkout with external first-party evidence.
    (tmp_path / ".git").mkdir()
    service = tmp_path / "services" / "api"
    service.mkdir(parents=True)
    (service / "app.py").write_text(
        "from common import daytona_client\ndaytona_client.create()\n"
    )
    (service / "requirements.txt").write_text("daytona<1\n")
    if parent_manifest:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="app"\ndependencies=["daytona<1"]\n'
        )
    else:
        common = tmp_path / "libs" / "common"
        common.mkdir(parents=True)
        (common / "__init__.py").write_text(
            "from daytona import Daytona\ndaytona_client = Daytona()\n"
        )
    # When upgrading only the service.
    status = main([str(service), "--force", "--json"])
    # Then exactly one project-level item warns about incomplete analysis.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert (
        len([item for item in report["items"] if item["kind"] == "types-unresolved"])
        == 1
    )


def test_reports_missing_git_when_binary_is_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a project in an environment without a git binary.
    (tmp_path / "requirements.txt").write_text("daytona<1\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    # When invoking the CLI with its normal safety check.
    status = main([str(tmp_path), "--json"])
    # Then users are told Git is missing, not that the checkout is absent.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert (
        "Git is not installed; review the changes and keep a backup."
        in report["nextSteps"]
    )


def test_names_target_when_atomic_write_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a nested target and a replacement error containing a staging filename.
    directory = tmp_path / "src"
    directory.mkdir()
    path = directory / "app.py"
    path.write_text("from daytona import Daytona\nDaytona().create()\n")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError(errno.EACCES, "Permission denied", str(source))

    monkeypatch.setattr(os, "replace", fail_replace)
    # When the real CLI attempts its transaction.
    status = main([str(tmp_path), "--force", "--json"])
    # Then both output channels name the project-relative target, never a temp path.
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert status == 1
    assert "src/app.py" in captured.err
    assert ".daytona-upgrade.tmp" not in captured.err + captured.out
    assert str(tmp_path) not in " ".join(report["nextSteps"])
