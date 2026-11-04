# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Detached factories and nested consumers retain unresolved config boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "member", ["model_copy", "copy", "model_validate", "parse_obj", "from_orm"]
)
def test_cli_refuses_when_bound_factory_escapes(tmp_path: Path, member: str) -> None:
    # Given a factory reference hiding a URL update from direct-call analysis.
    argument = (
        "update={'api_url': custom}"
        if member in {"model_copy", "copy"}
        else "{'api_url': custom}"
    )
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"f = cfg.{member}\nf({argument})\nDaytona(cfg).create()\n"
    )
    path = tmp_path / "app.py"
    path.write_text(source)
    # When applying through the real CLI without a Cloud assertion.
    status = cli.main([str(tmp_path), "--yes", "--force"])
    # Then unresolved references refuse atomically rather than silently migrating.
    assert status == 2
    assert path.read_text() == source


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize(
    "consumer", ["CLIENT(cfg).create()", "CLIENT(cfg).snapshot.list()"]
)
def test_origin_is_preserved_when_escaped_config_has_nested_consumer(
    client: str, consumer: str
) -> None:
    # Given an escaped config and calls sharing the constructor's start position.
    constructor = "DaytonaConfig(api_url=None, api_key='k')"
    source = (
        f"from daytona import {client}, DaytonaConfig\ncfg = {constructor}\n"
        "customize(cfg)\n" + consumer.replace("CLIENT", client) + "\n"
    )
    # When asserting Cloud usage for a config whose mutations remain unknown.
    result = migrate(source, drop=True)
    # Then the original config stays user-owned and its uncertainty is reported.
    assert constructor in result.source
    assert any(item["kind"] == "config-field" for item in result.items)


@pytest.mark.parametrize("member", ["model_copy", "copy"])
def test_factory_reference_stays_manual_when_cloud_is_asserted(member: str) -> None:
    # Given a detached factory on a config with a removable URL.
    constructor = "DaytonaConfig(api_url=None)"
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"cfg = {constructor}\nf = cfg.{member}\nDaytona(cfg).create()\n"
    )
    # When the operator explicitly accepts unresolved Cloud usage.
    result = migrate(source, drop=True)
    # Then the escape cannot authorize rewriting the original constructor.
    assert constructor in result.source
    assert any(item["kind"] == "config-field" for item in result.items)
