# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic-only config APIs are manual work, not evidence of a custom URL."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli

MEMBERS = (
    "model_dump",
    "model_dump_json",
    "model_copy",
    "model_validate",
    "model_validate_json",
    "model_fields",
    "model_fields_set",
    "model_config",
    "dict",
    "json",
    "copy",
    "parse_obj",
    "parse_raw",
    "schema",
    "schema_json",
    "construct",
    "__fields__",
)


@pytest.mark.parametrize("member", MEMBERS)
@pytest.mark.parametrize("receiver", ["cfg", "DaytonaConfig"])
@pytest.mark.parametrize("url", ["", ", api_url='https://custom.example/api'"])
def test_member_report_when_config_is_pydantic(
    member: str, receiver: str, url: str
) -> None:
    # Given a proven config/class, with independent URL evidence when requested.
    suffix = (
        ""
        if member in {"model_fields", "model_fields_set", "model_config", "__fields__"}
        else "()"
    )
    source = (
        "import logging\nfrom daytona import Daytona, DaytonaConfig\n"
        f"cfg = DaytonaConfig(api_key='k'{url})\n"
        f"logging.info('%s', {receiver}.{member}{suffix})\nDaytona(cfg).create()\n"
    )
    # When inspecting the removed Pydantic surface.
    result = migrate(source)
    # Then manual dataclass guidance replaces the false URL escape report.
    assert any(item["kind"] == "config-field" for item in result.items)
    assert any(item["kind"] == "custom-api-url" for item in result.items) == bool(url)
    assert "Daytona(cfg).v0.create()" in result.source


@pytest.mark.parametrize("receiver", ["cfg", "DaytonaConfig"])
@pytest.mark.parametrize(
    "member", ["model_copy", "copy", "model_validate", "parse_obj", "construct"]
)
@pytest.mark.parametrize(
    "payload,refused",
    [
        ("{'api_key': 'new'}", False),
        ("{'api_url': 'https://custom.example/api'}", True),
        ("{'server_url': 'https://custom.example/api'}", True),
        ("{'api_url': 'https://app.daytona.io/api'}", False),
        ("options", True),
    ],
)
def test_factory_url_checks_when_pydantic_result_is_used(
    receiver: str, member: str, payload: str, refused: bool
) -> None:
    # Given a copy/validation result passed into the real client constructor.
    argument = (
        "update=" + payload
        if member in {"model_copy", "copy"}
        else "**" + payload
        if member == "construct"
        else payload
    )
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"other = {receiver}.{member}({argument})\nDaytona(other).create()\n"
    )
    # When checking both the source config and the result's URL payload.
    result = migrate(source)
    # Then known custom URLs and unresolved payloads retain the safety fence.
    assert any(item["kind"] == "custom-api-url" for item in result.items) == refused
    assert any(item["kind"] == "config-field" for item in result.items)


@pytest.mark.parametrize(
    "member", ["model_copy", "copy", "model_validate", "parse_obj"]
)
@pytest.mark.parametrize(
    "payload", ["options", "{'api_url': 'https://custom.example/api'}"]
)
def test_cli_refusal_when_factory_can_carry_url(
    tmp_path: Path, member: str, payload: str
) -> None:
    # Given a factory result with an unknown or known-custom URL.
    argument = "update=" + payload if member in {"model_copy", "copy"} else payload
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona(cfg.{member}({argument})).create()\n"
    )
    path = tmp_path / "app.py"
    path.write_text(source)
    # When running the actual write surface.
    status = cli.main([str(tmp_path), "--yes", "--force"])
    # Then refusal remains atomic.
    assert status == 2
    assert path.read_text() == source


def test_cli_upgrade_when_dump_has_no_url(tmp_path: Path) -> None:
    # Given a read-only config use.
    source = (
        "import logging\nfrom daytona import Daytona, DaytonaConfig\n"
        'cfg = DaytonaConfig(api_key="k"); logging.info("%s", cfg.model_dump()); Daytona(cfg).create()\n'
    )
    path = tmp_path / "app.py"
    path.write_text(source)
    # When applying the upgrade without disabling URL checks.
    status = cli.main([str(tmp_path), "--yes", "--force"])
    # Then the migration proceeds with explicit manual config guidance.
    assert status == 0
    assert "Daytona(cfg).v0.create()" in path.read_text()
    assert "FIXME(daytona-upgrade)" in path.read_text()


@pytest.mark.parametrize("member", MEMBERS)
def test_unrelated_member_when_receiver_is_not_config(member: str) -> None:
    # Given a lookalike member on an unrelated object.
    source = f"from daytona import Daytona\nother.{member}()\nDaytona().create()\n"
    # When migrating other proven SDK calls.
    result = migrate(source)
    # Then the lookalike stays outside config policy.
    assert result.items == ()


@pytest.mark.parametrize(
    "member", ["model_copy", "copy", "model_validate", "parse_obj"]
)
def test_unknown_payload_when_cloud_is_asserted(member: str) -> None:
    # Given an unresolved factory input explicitly asserted to target Cloud.
    argument = "update=options" if member in {"model_copy", "copy"} else "options"
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona(cfg.{member}({argument})).create()\n"
    )
    # When the operator takes responsibility for the unresolved payload.
    result = migrate(source, drop=True)
    # Then the manual call stays visible without a URL refusal.
    assert not any(item["kind"] == "custom-api-url" for item in result.items)
    assert any(item["kind"] == "config-field" for item in result.items)
