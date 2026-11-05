# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Poetry alternatives retain every installation selector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit

from daytona_upgrade.cli import main
from daytona_upgrade.manifests import rewrite_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "section",
    [
        "tool.poetry.dependencies",
        "tool.poetry.group.dev.dependencies",
        "tool.poetry.dev-dependencies",
    ],
)
def test_preserves_selectors_when_poetry_has_multiple_constraints(section: str) -> None:
    # Given
    source = f"""[{section}]
daytona-sdk = [
  {{version='0.224',python='>=3.9,<3.11',extras=['otel'],optional=true}}, # first
  {{version='0.223',python='>=3.12',markers='sys_platform == "linux"',platform='linux'}},
]
"""
    # When
    result, changes = rewrite_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then
    document = tomlkit.parse(result).unwrap()
    for key in section.split("."):
        document = document[key]
    assert document == {
        "daytona": [
            {
                "version": ">=2,<3",
                "python": ">=3.9,<3.11",
                "extras": ["otel", "v0-sdk"],
                "optional": True,
            },
            {
                "version": ">=2,<3",
                "python": ">=3.12",
                "markers": 'sys_platform == "linux"',
                "platform": "linux",
                "extras": ["v0-sdk"],
            },
        ]
    }
    assert "# first" in result
    assert len(changes) == 1


@pytest.mark.parametrize(
    "alternatives",
    ['["0.224"]', "[]", "true"],
)
def test_reports_manual_when_poetry_alternative_is_unsupported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], alternatives: str
) -> None:
    # Given
    manifest = tmp_path / "pyproject.toml"
    source = f"[tool.poetry.dependencies]\ndaytona = {alternatives} # keep\n"
    manifest.write_text(source)
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["items"][0]["kind"] == "manual-dependency"
    assert len(report["items"]) == 1
    assert "daytona[v0-sdk]>=2,<3" in report["items"][0]["message"]
    assert manifest.read_text() == source
    assert report["dependencies"] == []


def test_migrates_table_when_another_poetry_alternative_is_manual(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "[tool.poetry.dependencies]\n"
        'daytona = [{version="0.224",python=">=3.9",extras=["otel"]}, "0.223"]\n'
    )
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert tomlkit.parse(manifest.read_text()).unwrap() == {
        "tool": {
            "poetry": {
                "dependencies": {
                    "daytona": [
                        {
                            "version": ">=2,<3",
                            "python": ">=3.9",
                            "extras": ["otel", "v0-sdk"],
                        },
                        "0.223",
                    ]
                }
            }
        }
    }
    assert [item["kind"] for item in report["items"]] == ["manual-dependency"]
    assert len(report["dependencies"]) == 1
