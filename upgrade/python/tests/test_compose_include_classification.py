# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose include classification: whole-list Compose shape, interpolated paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade import cli

APP = "from daytona import Daytona\nDaytona().create()\n"
REQ = "daytona==0.224.0\n"


def _run(
    tmp_path: Path, files: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, list[str]]:
    for name, content in {"app.py": APP, "requirements.txt": REQ, **files}.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    status = cli.main([str(tmp_path), "--json", "--dry", "--strict"])
    report = json.loads(capsys.readouterr().out)
    return status, [
        item["kind"]
        for item in report["items"]
        if item["kind"] in {"env-api-url", "custom-api-url"}
    ]


def test_mixed_include_list_is_not_compose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a CI pipeline whose include list has one Compose-shaped entry among CI entries.
    files = {
        "pipeline.yml": "include:\n  - local: ci/build.yml\n  - path: other.yml\n",
        "other.yml": "services:\n  app:\n    environment:\n      DAYTONA_API_URL: https://self.example/api\n",
    }
    # When migrating with --strict.
    status, kinds = _run(tmp_path, {**files}, capsys)
    # Then pipeline.yml is not traversed as Compose; other.yml is still found on its own (services:).
    assert kinds == ["env-api-url", "custom-api-url"]
    assert status == 2


def test_mixed_include_list_adds_no_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a CI include list with one Compose-shaped entry pointing at an unrelated file.
    files = {
        "pipeline.yml": "include:\n  - template: Jobs/Build.gitlab-ci.yml\n  - path: ci/missing.yml\n"
    }
    # When migrating with --strict.
    status, kinds = _run(tmp_path, files, capsys)
    # Then no environment review item is invented.
    assert kinds == []
    assert status == 0


@pytest.mark.parametrize(
    "entry", ["${MANIFEST}", '"${MANIFEST}"', "'${COMPOSE_DIR}/app.yml'"]
)
def test_interpolated_include_path_requires_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], entry: str
) -> None:
    # Given a pipeline whose only include is an interpolated path.
    files = {"pipeline.yml": f"include:\n  - {entry}\n"}
    # When migrating with --strict.
    status, kinds = _run(tmp_path, files, capsys)
    # Then the unresolved Compose include is reviewed, never silently skipped.
    assert kinds == ["env-api-url"]
    assert status == 1


def test_alias_only_include_stays_unclassified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a non-Compose pipeline whose include is an alias.
    files = {"pipeline.yml": "x: &shared [ci/build.yml]\ninclude: *shared\n"}
    # When migrating with --strict.
    status, kinds = _run(tmp_path, files, capsys)
    # Then it is not treated as Compose and adds no review item.
    assert kinds == []
    assert status == 0
