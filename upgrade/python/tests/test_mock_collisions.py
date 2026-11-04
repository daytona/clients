# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Namespace configuration conflicts fence moved mock members and their aliases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "configuration",
    [
        'v0=None, **{"create.return_value": 42}',
        '**{"create.return_value": 42}, v0=None',
        '**{"v0": None, "create.return_value": 42}',
        '**{"v0.create": replacement, "create.return_value": 42}',
        '**{"v0.snapshot": replacement, "snapshot.get.return_value": 42}',
        '**{"v0.other": replacement, "create.return_value": 42}',
    ],
)
def test_requires_review_when_root_v0_configuration_accompanies_moved_key(
    configuration: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a namespace override and a moved key in the same configuration call.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\nalias = client\n"
        f"client.configure_mock({configuration})\n"
        "client.create()\nalias.create()\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When strict migration considers both configuration and later alias calls.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then setup stays unchanged and every alias loses automatic routing proof.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert [item["kind"] for item in report["items"]] == ["mock-target"]
