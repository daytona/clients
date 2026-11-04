# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Kubernetes flow-style endpoint safety through the real CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

from daytona_upgrade.cli import main

SETTINGS: Final = [
    "env: [{name: DAYTONA_API_URL, value: ENDPOINT}]",
    'env:\n  - {name: DAYTONA_API_URL, value: "ENDPOINT"}',
    "env: [{name: 'DAYTONA_SERVER_URL', value: 'ENDPOINT', other: ok}] # trailing",
]


@pytest.mark.parametrize("setting", SETTINGS)
def test_refuses_without_writing_when_kubernetes_flow_value_is_custom(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], setting: str
) -> None:
    # Given
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.224.0\n")
    deployment = tmp_path / "k8s" / "app.yaml"
    deployment.parent.mkdir()
    source = setting.replace("ENDPOINT", "https://self.hosted")
    deployment.write_text(source)
    # When
    code = main([str(tmp_path), "--json", "--yes"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert requirements.read_text() == "daytona==0.224.0\n"
    assert deployment.read_text() == source


@pytest.mark.parametrize("setting", SETTINGS)
def test_accepts_when_kubernetes_flow_value_is_cloud(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], setting: str
) -> None:
    # Given
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    deployment = tmp_path / "k8s" / "app.yaml"
    deployment.parent.mkdir()
    deployment.write_text(setting.replace("ENDPOINT", "https://app.daytona.io/api"))
    # When
    code = main([str(tmp_path), "--json", "--yes"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [item["kind"] for item in report["items"]] == ["env-api-url"]
