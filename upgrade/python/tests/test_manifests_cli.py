# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Uv overrides and slim-container CLI safety regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize("package", ["daytona", "daytona-sdk", "daytona_sdk"])
@pytest.mark.parametrize(
    "override",
    [
        '{ git = "https://example.test/sdk", tag = "v0.224.0" }',
        '{ path = "../sdk" }',
        '{ url = "https://example.test/sdk.whl" }',
        '[{ path = "../sdk", marker = "sys_platform == \'linux\'" }]',
    ],
)
@pytest.mark.parametrize("version", ["==0.224.0", "[v0-sdk]>=2,<3"])
def test_manifest_requires_manual_work_when_uv_overrides_sdk(
    package: str, override: str, version: str
) -> None:
    # Given a source override that can keep the resolved SDK at 0.x.
    source = (
        f'[project]\ndependencies = ["{package}{version}"]\n'
        f"[tool.uv.sources]\n{package} = {override}\n"
    )
    # When inspecting the dependency migration.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then resolution is manual, unchanged, and never certifies an ancestor upgrade.
    assert result.manual
    assert result.found
    assert not result.v2_found
    assert result.source == source
    assert result.changes == ()


@pytest.mark.parametrize("force", [False, True])
def test_cli_proceeds_with_note_when_git_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    force: bool,
) -> None:
    # Given an actual PATH without git, not a mocked subprocess.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    # When running a writing migration, with either force setting.
    status = main([str(tmp_path), "--json", "--yes", *(["--force"] if force else [])])
    # Then missing git is noted unless inspection was explicitly bypassed.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert "Daytona().v0.create()" in app.read_text()
    assert (
        "Git is not installed; review the changes and keep a backup."
        in report["nextSteps"]
    ) is not force


@pytest.mark.parametrize("variable", ["DAYTONA_API_URL", "DAYTONA_SERVER_URL"])
def test_cli_uses_shared_environment_message_when_reporting_setting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], variable: str
) -> None:
    # Given an environment declaration of either supported variable.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / ".env").write_text(f"{variable}=https://app.daytona.io/api\n")
    # When emitting a read-only JSON migration report.
    status = main([str(tmp_path), "--dry", "--json"])
    # Then the shared public report names the actual variable, like TypeScript.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"][0]["message"] == (
        f"{variable} is ignored by SDK 2.x: .v0 targets Daytona Cloud, and .v2 reads only DAYTONA_V2_API_URL, for test environments. Review this setting."
    )


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_uses_shared_refusal_when_tree_is_dirty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
) -> None:
    # Given a narrow git wire fake reporting a dirty working tree.
    git = tmp_path / "git"
    git.write_text("#!/bin/sh\necho ' M app.py'\n")
    git.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    # When attempting a writing run without --force.
    status = main([str(tmp_path), *(["--json"] if json_output else [])])
    # Then JSON shares TS's message and text output shares its Refused prefix.
    output = capsys.readouterr()
    message = (
        "Git tree has uncommitted changes. Commit or stash them, or run with --force."
    )
    assert status == 2
    if json_output:
        assert json.loads(output.out)["refused"]["message"] == message
    else:
        assert output.err == f"Refused: {message}\n"
