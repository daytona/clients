# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Case-insensitive Cloud identity shared by config validation and rewriting."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "url", ["HTTPS://APP.DAYTONA.IO/api", "https://APP.DAYTONA.IO/api/"]
)
@pytest.mark.parametrize(
    "configure",
    [
        "cfg = DaytonaConfig(api_url=URL)",
        "cfg = DaytonaConfig(**{'server_url': URL})",
        "cfg = DaytonaConfig()\ncfg.api_url = URL",
        "cfg = DaytonaConfig()\nsetattr(cfg, 'server_url', URL)",
    ],
)
def test_migrates_when_cloud_config_host_uses_uppercase(
    tmp_path: Path, url: str, configure: str
) -> None:
    # Given a bound config used by the client, not merely an unused literal.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        + configure.replace("URL", repr(url))
        + "\nDaytona(cfg).create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the real CLI upgrades the project.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then config validation and rewriting agree the endpoint is Cloud.
    assert status == 0
    assert "Daytona(cfg).v0.create()" in app.read_text()
    assert url not in app.read_text()


@pytest.mark.parametrize(
    "url", ["HTTPS://APP.DAYTONA.IO/API", "https://app.daytona.io.evil/api"]
)
def test_refuses_when_config_path_or_host_differs(tmp_path: Path, url: str) -> None:
    # Given a URL whose case-sensitive path or hostname differs from Cloud.
    source = (
        f"from daytona import DaytonaConfig\ncfg = DaytonaConfig(api_url={url!r})\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    # When the caller asserts the environment is Cloud.
    status = main([str(tmp_path), "--force", "--yes", "--drop-api-url"])
    # Then a proven custom config endpoint remains unoverridable.
    assert status == 2
    assert app.read_text() == source
