# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Definition-time config ownership and patched builtin observation fences."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "definition",
    [
        "def run(self, client=Daytona(cfg)): pass",
        "async def run(self, *, client=Daytona(cfg)): pass",
        "run = lambda client=Daytona(cfg): client",
    ],
)
def test_config_is_unverified_when_method_default_uses_escaped_class_config(
    definition: str,
) -> None:
    # Given a clean module config shadowed by an escaped class-local config.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        "class Service:\n    cfg = DaytonaConfig()\n    customize(cfg)\n"
        f"    {definition}\n"
    )
    # When resolving the default in the class body where it executes.
    result = migrate(source)
    # Then the clean module config cannot hide the class-local escape.
    assert any(item["kind"] == "custom-api-url" for item in result.items)


@pytest.mark.parametrize("builtin", ["print", "repr", "str"])
@pytest.mark.parametrize("reader", ["READER(cfg)", "emit(cfg)"])
@pytest.mark.parametrize("foreign", [False, True])
def test_config_is_unverified_when_builtin_reader_is_patched(
    tmp_path: Path, builtin: str, reader: str, foreign: bool
) -> None:
    # Given a project which replaces the apparent read-only builtin with a mutator.
    patch = (
        "import builtins as b\ndef mutate(value):\n"
        "    value.api_url = 'https://custom.example/api'\n"
        f"b.{builtin} = mutate\n"
    )
    source = (
        f"from builtins import {builtin} as emit\n"
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + reader.replace("READER", builtin)
        + "\nDaytona(cfg).create()\n"
    )
    if foreign:
        (tmp_path / "hooks.py").write_text(patch)
    else:
        source = patch + source
    app = tmp_path / "app.py"
    app.write_text(source)
    # When preflighting all project files through the real CLI.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then a replaced builtin is an escape and atomic refusal preserves the file.
    assert status == 2
    assert app.read_text() == source


def test_config_stays_verified_when_another_builtin_is_patched() -> None:
    # Given a patched print but an unchanged repr observation.
    source = (
        "import builtins\nfrom daytona import Daytona, DaytonaConfig\n"
        "builtins.print = mutate\ncfg = DaytonaConfig()\nrepr(cfg)\nDaytona(cfg)\n"
    )
    # When checking trust by builtin identity rather than a project-wide flag.
    result = migrate(source)
    # Then independent read-only builtins remain supported.
    assert result.items == ()


def test_config_item_requests_manual_review_when_drop_flag_is_already_set() -> None:
    # Given an unresolved caller-owned config and an explicit Cloud assertion.
    source = "from daytona import Daytona\ndef run(cfg):\n    Daytona(cfg)\n"
    # When migrating under --drop-api-url.
    result = migrate(source, drop=True)
    # Then the report asks for review, not another run with the same flag.
    assert [item["kind"] for item in result.items] == ["config-field"]
    assert result.items[0]["message"] == (
        "this DaytonaConfig's API URL could not be verified; --drop-api-url keeps "
        "the call as is — confirm it targets Daytona Cloud"
    )
