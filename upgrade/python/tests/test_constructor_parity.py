# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Own constructor arguments are not SDK configs (TypeScript parity cases)."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "config",
    [
        "DaytonaConfig(api_key=key)",
        'DaytonaConfig(api_key=key, api_url="https://app.daytona.io/api")',
    ],
)
@pytest.mark.parametrize("argument", ['"k"', "4"])
def test_migrates_when_subclass_accepts_non_config_argument(
    tmp_path: Path, config: str, argument: str
) -> None:
    # Given
    app = tmp_path / "app.py"
    app.write_text(
        "from daytona import Daytona, DaytonaConfig\n"
        "class Custom(Daytona):\n"
        "    def __init__(self, key):\n"
        f"        super().__init__({config})\n"
        f"Custom({argument}).create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 0
    assert f"Custom({argument}).v0.create()" in app.read_text()
    assert "api_url" not in app.read_text()
