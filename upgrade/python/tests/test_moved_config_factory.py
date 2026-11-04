# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The formerly unresolved literal-config factory now upgrades normally."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("member", ["", ".create()"])
def test_project_migrates_when_config_factory_returns_literal_without_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], member: str
) -> None:
    # Given the moved case and its ordinary client-call variant.
    # A direct literal config factory with no URL field is followed like a client
    # factory.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        "def config_factory():\n    return DaytonaConfig()\n"
        f"Daytona(config_factory()){member}\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.224.0\n")
    # When migrating without asserting any unresolved URL is Cloud.
    status = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then the exact project succeeds, routing moved calls and upgrading its pin.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert "refused" not in report
    assert report["items"] == []
    assert app.read_text() == source.replace(
        "Daytona(config_factory()).create()", "Daytona(config_factory()).v0.create()"
    )
    assert requirements.read_text() == "daytona[v0-sdk]>=2,<3\n"
