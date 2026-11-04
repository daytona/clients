# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Representation is not read-only when the config class is patched."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "hook", ["__repr__", "__str__", "__format__", "__getattribute__"]
)
@pytest.mark.parametrize(
    "reader",
    [
        "print(cfg)",
        "repr(cfg)",
        "str(cfg)",
        "text = f'{cfg}'",
        "logging.info(cfg)",
        "json.dumps(cfg, default=str)",
    ],
)
def test_config_is_unverified_when_representation_hooks_are_patched(
    hook: str, reader: str
) -> None:
    # Given a hook capable of changing the config during an apparent observation.
    source = (
        "import json, logging\nfrom daytona import Daytona, DaytonaConfig\n"
        "def mutate(self, *args):\n    self.api_url = 'https://self.example/api'\n    return ''\n"
        f"DaytonaConfig.{hook} = mutate\ncfg = DaytonaConfig()\n{reader}\nDaytona(cfg).create()\n"
    )
    # When inspecting config uses.
    result = migrate(source)
    # Then representation calls cannot justify Cloud provenance.
    assert any(item["kind"] == "custom-api-url" for item in result.items)


def test_config_is_unverified_when_hook_is_patched_in_another_module(
    tmp_path: Path,
) -> None:
    # Given a project-level patch even though the local config module only prints.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        "print(cfg)\nDaytona(cfg).create()\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "hooks.py").write_text(
        "from daytona import DaytonaConfig as C\n"
        "def mutate(self):\n    self.api_url = 'https://self.example/api'\n    return ''\n"
        "C.__repr__ = mutate\n"
    )
    # When running the CLI's complete project preflight.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then the patch also fences readers in the other module.
    assert status == 2
    assert (tmp_path / "app.py").read_text() == source
