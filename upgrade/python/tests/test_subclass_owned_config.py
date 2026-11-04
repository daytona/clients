# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Subclass-owned configs are checked at their actual construction boundary."""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "scenario",
    product(["Daytona", "AsyncDaytona"], [False, True], [False, True], [False, True]),
)
def test_migrates_when_subclass_constructs_own_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    scenario: tuple[str, bool, bool, bool],
) -> None:
    # Given a constructor taking an API key, not a caller-owned config.
    client, local, annotated, inherited = scenario
    construction = (
        (
            "        config: DaytonaConfig = DaytonaConfig(api_key=api_key)\n"
            if annotated
            else "        config = DaytonaConfig(api_key=api_key)\n"
        )
        + "        super().__init__(config)\n"
        if local
        else "        super().__init__(DaytonaConfig(api_key=api_key))\n"
    )
    source = (
        f"import os\nfrom daytona import {client}, DaytonaConfig\n"
        f"class MyClient({client}):\n    def __init__(self, api_key):\n"
        + construction
        + ("class Child(MyClient): pass\n" if inherited else "")
        + f"client = {'Child' if inherited else 'MyClient'}(os.environ['K'])\n"
        + "client.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When upgrading through the actual strict CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then the config is accepted and the inherited SDK call migrates.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert app.read_text() == source.replace("client.create()", "client.v0.create()")


@pytest.mark.parametrize(
    "body,argument",
    [
        (
            "config.api_url = os.environ['URL']\n        super().__init__(config)",
            "DaytonaConfig()",
        ),
        (
            "config = DaytonaConfig(api_key=api_key)\n"
            "        config.api_url = os.environ['URL']\n        super().__init__(config)",
            "os.environ['K']",
        ),
        (
            "super().__init__(DaytonaConfig(api_key=api_key, api_url=os.environ['URL']))",
            "os.environ['K']",
        ),
        (
            "super().__init__(DaytonaConfig(api_key=mutate(config)))",
            "DaytonaConfig()",
        ),
    ],
)
def test_refuses_when_subclass_can_mutate_or_supply_url(
    tmp_path: Path, body: str, argument: str
) -> None:
    # Given either caller-owned config mutation or an unsafe locally built config.
    parameter = "config" if argument == "DaytonaConfig()" else "api_key"
    source = (
        "import os\nfrom daytona import Daytona, DaytonaConfig\n"
        f"class MyClient(Daytona):\n    def __init__(self, {parameter}):\n"
        f"        {body}\nMyClient({argument}).create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)

    # When checking URL safety without an explicit Cloud assertion.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then the whole run refuses without changing the program.
    assert status == 2
    assert app.read_text() == source


@pytest.mark.parametrize("mutating_base", [False, True])
def test_constructor_proof_survives_imports_when_config_is_owned(
    tmp_path: Path, mutating_base: bool
) -> None:
    # Given an imported factory, optionally delegating to a mutating project base.
    base = (
        "class Base(Daytona):\n    def __init__(self, config):\n"
        "        config.api_url = url\n        super().__init__(config)\n"
        if mutating_base
        else "Base = Daytona\n"
    )
    (tmp_path / "factory.py").write_text(
        "from daytona import Daytona, DaytonaConfig\n"
        + base
        + "class MyClient(Base):\n    def __init__(self, api_key):\n"
        "        super().__init__(DaytonaConfig(api_key=api_key))\n"
    )
    source = (
        "import os\nfrom factory import MyClient\nMyClient(os.environ['K']).create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When upgrading all project modules through the CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict"])

    # Then only a safe inherited construction chain permits the upgrade.
    assert status == (2 if mutating_base else 0)
    assert app.read_text() == (
        source if mutating_base else source.replace(").create()", ").v0.create()")
    )
