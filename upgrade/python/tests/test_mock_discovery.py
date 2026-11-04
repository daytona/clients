# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mocks-only source scopes count as SDK use without a direct SDK import."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "target",
    [
        "daytona_sdk.Daytona",
        "daytona.Daytona",
        "daytona_sdk.common.errors.DaytonaError",
    ],
)
def test_completes_scan_when_sdk_is_used_only_by_mock_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    # Given a single tests-only file and no dependency manifest or SDK import.
    file = tmp_path / "test_app.py"
    file.write_text(
        f"from unittest.mock import patch\n@patch('{target}')\ndef test_case(mock): pass\n"
    )
    # When the real CLI identifies a supported SDK patch path.
    code = main([str(file), "--yes", "--json", "--force"])
    report = json.loads(capsys.readouterr().out)
    # Then its obligations are delivered in a completed run, not a no-Daytona refusal.
    assert code == 0, report
    assert "refused" not in report
    assert any(
        item["kind"] == "mock-target" for item in report["items"]
    ) == target.endswith("Daytona")
    assert "daytona_sdk" not in file.read_text()


def test_refuses_scan_when_mock_target_is_unrelated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a genuine patch call whose literal mentions no recognized SDK target.
    file = tmp_path / "test_app.py"
    file.write_text("from unittest.mock import patch\npatch('other.Factory')\n")
    # When scanning the isolated file.
    code = main([str(file), "--yes", "--json", "--force"])
    report = json.loads(capsys.readouterr().out)
    # Then patch syntax alone does not establish Daytona presence.
    assert code == 2
    assert report["refused"]["reason"] == "no-daytona"
