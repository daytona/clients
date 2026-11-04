# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency migrations cannot silently leave rewritten code on SDK 0.x."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "section",
    [
        '[tool.uv]\ndev-dependencies = ["daytona-sdk==0.120.0"] # keep\n',
        '[tool.pdm.dev-dependencies]\ndev = ["daytona-sdk==0.120.0"] # keep\n',
        '[tool.pdm.dev-dependencies]\ntest = ["pytest", "daytona-sdk==0.120.0"] # keep\n',
    ],
)
def test_migrates_requirement_when_legacy_dev_group(section: str) -> None:
    # Given a legacy development dependency declaration.
    # When the manifest adapter inspects it.
    result = inspect_manifest(Path("pyproject.toml"), section, load_mapping())
    # Then the dependency changes without changing unrelated formatting.
    assert result.source == section.replace(
        "daytona-sdk==0.120.0", "daytona[v0-sdk]>=2,<3"
    )
    assert result.found
    assert len(result.changes) == 1


def test_strict_reports_dependency_when_source_rewritten_without_declaration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given source whose requirement is declared outside supported manifests.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    # When the real CLI upgrades the code in strict mode.
    status = main([str(tmp_path), "--json", "--dry", "--strict"])
    # Then the unhandled dependency is visible to automation.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["manual-dependency"]
    assert report["changedFiles"] == ["app.py"]


@pytest.mark.parametrize(
    "requirement", ["daytona==0.120.0\n", "daytona[v0-sdk]>=2,<3\n", "daytona => 0.2\n"]
)
def test_reports_only_existing_dependency_action_when_declaration_known(
    tmp_path: Path, requirement: str
) -> None:
    # Given a requirement already upgraded, migratable, or explicitly manual.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    (tmp_path / "requirements.txt").write_text(requirement)
    # When calculating the project migration.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the fallback does not duplicate an existing dependency decision.
    expected_manual = 1 if "=>" in requirement else 0
    assert (
        sum(item["kind"] == "manual-dependency" for item in result.items)
        == expected_manual
    )


@pytest.mark.parametrize("mode", ["suggest-v2", "remove-suggestions"])
def test_skips_dependency_guard_when_only_comments_change(
    tmp_path: Path, mode: str
) -> None:
    # Given code used only for suggestion rendering or cleanup.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    # When operating in a non-upgrade mode.
    options = TransformOptions(
        load_mapping(), "suggest-v2" if mode == "suggest-v2" else "remove-suggestions"
    )
    result = preflight(tmp_path, options)
    # Then no SDK dependency update is required by those comments.
    assert not any(item["kind"] == "manual-dependency" for item in result.items)


@pytest.mark.parametrize("requirement", ["daytona==0.120.0", '"daytona[v0-sdk]>=2,<3"'])
def test_skips_fallback_when_notebook_declares_daytona(
    tmp_path: Path, requirement: str
) -> None:
    # Given an executable notebook install rather than a manifest requirement.
    source = (
        f"%pip install {requirement}\nfrom daytona import Daytona\nDaytona().create()\n"
    )
    (tmp_path / "app.ipynb").write_text(
        json.dumps({"cells": [{"cell_type": "code", "source": source}]})
    )
    # When migrating the notebook and its declared requirement together.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then there is no missing-dependency action for the already handled install.
    assert not any(item["kind"] == "manual-dependency" for item in result.items)
