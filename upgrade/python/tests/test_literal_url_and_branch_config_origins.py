# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for literal URL policy and branch-local config origins."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize("drop", [False, True])
@pytest.mark.parametrize(
    "write",
    [
        "cfg.api_url = 'https://self.example/api'",
        "cfg.server_url: str = 'https://self.example/api'",
        "setattr(cfg, 'api_url', 'https://self.example/api')",
        "setattr(cfg, 'server_url', 'https://self.example/api')",
        "cfg = DaytonaConfig(api_url='https://self.example/api')",
        "def later():\n    cfg.api_url = 'https://self.example/api'",
        "def later():\n    global cfg\n    cfg.api_url = 'https://self.example/api'",
        "def later():\n    setattr(cfg, 'api_url', 'https://self.example/api')",
        "def build():\n    cfg = DaytonaConfig()\n    def later():\n        nonlocal cfg\n        cfg.api_url = 'https://self.example/api'\n    return Daytona(cfg)",
    ],
)
def test_cli_refuses_when_config_url_is_literal_custom(
    tmp_path: Path, drop: bool, write: str
) -> None:
    # Given a provable self-hosted URL and an old dependency pin.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + write
        + "\nDaytona(cfg).create()\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When preflighting with or without the non-literal URL assertion.
    status = cli.main(
        [str(tmp_path), "--force", "--yes", *(["--drop-api-url"] if drop else [])]
    )
    # Then the refusal leaves source and dependency unchanged atomically.
    assert status == 2
    assert (tmp_path / "app.py").read_text() == source
    assert (tmp_path / "requirements.txt").read_text() == "daytona==0.224.0\n"


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize("assignment", ["cfg =", "cfg: DaytonaConfig ="])
@pytest.mark.parametrize("suite", ["if enabled:", "for item in items:", "with lock:"])
def test_constructor_migrates_when_nested_config_has_use_site_proof(
    client: str, assignment: str, suite: str
) -> None:
    # Given construction and use within the same lexical control-flow block.
    source = (
        f"from daytona import {client}, DaytonaConfig\n{suite}\n"
        f"    {assignment} DaytonaConfig(api_url=None)\n    {client}(cfg).create()\n"
    )
    # When migration checks the config's provenance at its actual use.
    result = migrate(source)
    # Then the supported default migrates without a manual URL item.
    assert result.source == source.replace("api_url=None", "").replace(
        ".create()", ".v0.create()"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "if enabled:\n    cfg = DaytonaConfig()\nDaytona(cfg).create()\n",
        "if enabled:\n    cfg = DaytonaConfig()\nelse:\n    Daytona(cfg).create()\n",
        "if enabled:\n    cfg = DaytonaConfig()\n    customize(cfg)\n    Daytona(cfg).create()\n",
    ],
)
def test_constructor_refuses_when_nested_origin_lacks_use_site_proof(body: str) -> None:
    # Given a conditional origin without a dominating, unescaped use-site proof.
    source = "from daytona import Daytona, DaytonaConfig\n" + body
    # When preflighting the constructor boundary.
    result = migrate(source)
    # Then lexical containment alone does not authorize migration.
    assert "custom-api-url" in [item["kind"] for item in result.items]


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize("argument", ["None", "config=None"])
def test_cli_migrates_when_config_is_literal_none(
    tmp_path: Path, client: str, argument: str
) -> None:
    # Given 0.x's explicit spelling of an omitted client configuration.
    source = f"from daytona import {client}\n{client}({argument}).create()\n"
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When applying the default migration without an URL assertion.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then the client call and dependency upgrade normally.
    assert status == 0
    assert (tmp_path / "app.py").read_text() == source.replace(
        ".create()", ".v0.create()"
    )
    assert (tmp_path / "requirements.txt").read_text() == "daytona[v0-sdk]>=2,<3\n"
