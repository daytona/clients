# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal config-returning functions obey the existing constructor URL policy."""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "config",
    list(
        product(
            ["", " -> DaytonaConfig"],
            ["api_key='k'", "api_key='k', api_url='https://app.daytona.io/api'"],
        )
    ),
)
def test_upgrade_proceeds_when_factory_returns_literal_cloud_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], config: tuple[str, str]
) -> None:
    # Given a literal config returned by a project-local function.
    annotation, fields = config
    app = tmp_path / "app.py"
    app.write_text(
        "from daytona import Daytona, DaytonaConfig\n"
        f"def make_config(){annotation}: return DaytonaConfig({fields})\n"
        "Daytona(make_config()).list()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When invoking the real strict migration surface.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])
    # Then literal config provenance is sufficient without an override flag.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert "Daytona(make_config()).v0.list()" in app.read_text()
    assert "api_url" not in app.read_text()


@pytest.mark.parametrize("url", ["'https://custom.example/api'", "endpoint"])
def test_upgrade_refuses_when_factory_config_url_is_custom_or_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    # Given a factory whose constructor cannot establish a Cloud target.
    app = tmp_path / "app.py"
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"def make_config(): return DaytonaConfig(api_url={url})\n"
        "Daytona(make_config()).list()\n"
    )
    app.write_text(source)
    # When migrating without an assertion about unknown URLs.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then constructor policy still refuses and leaves every source unchanged.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert app.read_text() == source


@pytest.mark.parametrize(
    "factory",
    [
        "def make_config(config: DaytonaConfig): return config\n",
        "@decorate\ndef make_config(): return DaytonaConfig()\n",
        "def make_config(DaytonaConfig): return DaytonaConfig()\n",
        "def make_config(): return DaytonaConfig()\nmake_config = opaque\n",
        "def make_config() -> DaytonaConfig: return DaytonaConfig()\nDaytonaConfig = opaque\n",
    ],
)
def test_factory_remains_unresolved_when_literal_origin_is_unproven(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], factory: str
) -> None:
    # Given an annotation, shadow, decorator, or rebind instead of a proven origin.
    app = tmp_path / "app.py"
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        + factory
        + "Daytona(make_config()).list()\n"
    )
    app.write_text(source)
    # When checking the same constructor boundary.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then no annotation or syntactic return alone certifies URL safety.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert app.read_text() == source


@pytest.mark.parametrize(
    "consumer",
    ["Daytona(make_config()).list()", "cfg = make_config()\nDaytona(cfg).list()"],
)
def test_upgrade_proceeds_when_literal_config_factory_is_imported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], consumer: str
) -> None:
    # Given a factory whose literal constructor is inspected in another module.
    (tmp_path / "provider.py").write_text(
        "from daytona import DaytonaConfig\n"
        "def make_config(): return DaytonaConfig(api_key='k')\n"
    )
    app = tmp_path / "app.py"
    app.write_text(
        "from daytona import Daytona\nfrom provider import make_config\n"
        + consumer
        + "\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When crossing the import and optional config-variable boundaries.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])
    # Then both paths retain literal origin proof while using ordinary URL checks.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert ".v0.list()" in app.read_text()
