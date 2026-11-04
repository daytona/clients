# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Real writing CLI coverage for dependency fixes and manual diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit

from daytona_upgrade.cli import main


def test_cli_keeps_extras_resolvable_when_renaming_poetry_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a complete optional Poetry declaration used by an application extra.
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        '[tool.poetry]\nname = "example"\nversion = "1.0"\n'
        'description = "example"\nauthors = ["Example <example@example.test>"]\n'
        '[tool.poetry.dependencies]\npython = ">=3.10"\n'
        'daytona-sdk = {version="0.224", optional=true}\n'
        '[tool.poetry.extras]\nagent = ["Daytona_SDK"]\n'
    )
    # When executing the actual writing CLI twice.
    status = main([str(tmp_path), "--force", "--yes", "--json"])
    report = json.loads(capsys.readouterr().out)
    migrated = manifest.read_bytes()
    second_status = main([str(tmp_path), "--force", "--yes", "--json"])
    capsys.readouterr()
    # Then the extra resolves, the run is clean, and the second run is idempotent.
    document = tomlkit.parse(manifest.read_text()).unwrap()
    assert status == second_status == 0
    assert report["items"] == []
    assert document["tool"]["poetry"]["extras"]["agent"] == ["daytona"]
    assert "daytona" in document["tool"]["poetry"]["dependencies"]
    assert manifest.read_bytes() == migrated


@pytest.mark.parametrize(
    ("name", "source"),
    [
        (
            "pyproject.toml",
            "[tool.poetry.dependencies]\n"
            'daytona-sdk = {git="https://example.test/sdk", subdirectory="sdk-python"}\n',
        ),
        (
            "setup.cfg",
            "[options]\npython_requires = >=3.9\ninstall_requires = daytona==0.224\n",
        ),
        (
            "setup.py",
            'from setuptools import setup\nsetup(python_requires=">=3.9", install_requires=["daytona==0.224"])\n',
        ),
    ],
)
def test_cli_reports_manual_dependency_when_manifest_needs_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, source: str
) -> None:
    # Given a supported manifest needing a conservative source or floor review.
    manifest = tmp_path / name
    manifest.write_text(source)
    # When requesting a strict writing migration through the public CLI.
    status = main([str(tmp_path), "--force", "--yes", "--json", "--strict"])
    # Then strict mode reports a located dependency task rather than silent success.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(
        item["kind"] == "manual-dependency" and item["file"] == name
        for item in report["items"]
    )
    if name == "pyproject.toml":
        assert manifest.read_text() == source
