# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Cloud config reads resolve without weakening mutation or escape fences."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "body",
    [
        "def main():\n    Daytona(cfg).create()\n",
        "class Service:\n    def __init__(self):\n        self.client = Daytona(cfg)\n        self.client.create()\n",
        "class Service:\n    def run(self):\n        Daytona(cfg).create()\n",
        "def outer():\n    def inner():\n        Daytona(cfg).create()\n",
        "def unrelated(cfg):\n    customize(cfg)\ndef main():\n    Daytona(cfg).create()\n",
        "class Service:\n    cfg = unrelated\n    def run(self):\n        Daytona(cfg).create()\n",
    ],
)
@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
def test_module_config_resolves_when_used_in_nested_scope(
    body: str, client: str
) -> None:
    # Given an unescaped module config shared with a nested constructor.
    source = (
        f"from daytona import {client}, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + body.replace("Daytona(", client + "(")
    )
    # When migrating the nested call.
    result = migrate(source)
    # Then lexical ownership is enough to prove Cloud usage.
    assert result.source == source.replace(".create()", ".v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "read",
    [
        "print(cfg)",
        "text = repr(cfg)",
        "text = str(cfg)",
        "text = f'{cfg!r}'",
        "key = cfg.api_key",
        "consume(cfg.api_key)",
        "print(cfg.api_url)",
        "logging.info(cfg)",
        "logger.info(cfg)",
        "logger.debug('configuration: %s', cfg)",
        "log_info(cfg)",
        "text = json.dumps(cfg)",
        "text = json.dumps(cfg, default=str)",
        "text = dumps(cfg)",
        "emit(cfg)",
    ],
)
def test_config_resolves_when_prior_use_is_readonly(read: str) -> None:
    # Given proven standard-library readers and a direct config origin.
    source = (
        "import logging, json\nfrom json import dumps\nfrom logging import info as log_info\nfrom builtins import print as emit\n"
        "from daytona import Daytona, DaytonaConfig\n"
        "logger = logging.getLogger(__name__)\ncfg = DaytonaConfig()\n"
        + read
        + "\nDaytona(cfg).create()\n"
    )
    # When migration inspects prior config uses.
    result = migrate(source)
    # Then observation does not make the config escape.
    assert result.source == source.replace(".create()", ".v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "value", ["None", "'https://app.daytona.io/api'", "'https://app.daytona.io/api/'"]
)
@pytest.mark.parametrize(
    "write",
    [
        "cfg.api_url = VALUE",
        "cfg.server_url: str = VALUE",
        "setattr(cfg, 'api_url', VALUE)",
    ],
)
def test_cli_drops_default_url_write_when_config_is_real(
    tmp_path: Path, value: str, write: str
) -> None:
    # Given a supported Cloud/default mutation, not an unrelated URL property.
    write = write.replace("VALUE", value)
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + write
        + "\nDaytona(cfg).create()\n"
    )
    path = tmp_path / "app.py"
    path.write_text(source)
    # When running the real CLI without a Cloud assertion.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then removal leaves a usable root config and migrated resource call.
    assert status == 0
    assert write not in path.read_text()
    assert "Daytona(cfg).v0.create()" in path.read_text()


@pytest.mark.parametrize(
    "body",
    [
        "customize(cfg)\nDaytona(cfg)\n",
        "Daytona(cfg)\ndef later():\n    cfg.api_url = url\n",
        "def build():\n    Daytona(cfg)\ncfg.api_url = url\n",
        "def build():\n    Daytona(cfg)\ndef later():\n    setattr(cfg, 'api_url', url)\n",
        "def print(value):\n    value.api_url = url\nprint(cfg)\nDaytona(cfg)\n",
        "def repr(value):\n    value.api_url = url\nrepr(cfg)\nDaytona(cfg)\n",
        "def str(value):\n    value.api_url = url\nstr(cfg)\nDaytona(cfg)\n",
        "logger = custom\nlogger.info(cfg)\nDaytona(cfg)\n",
        "json = custom\njson.dumps(cfg)\nDaytona(cfg)\n",
        "json.dumps(cfg, default=customize)\nDaytona(cfg)\n",
        "json.dumps(cfg, cls=CustomEncoder)\nDaytona(cfg)\n",
        "json.dumps(cfg, **options)\nDaytona(cfg)\n",
        "def logging(value):\n    pass\nlogging(cfg)\nDaytona(cfg)\n",
        "def build(print):\n    print(cfg)\n    Daytona(cfg)\n",
        "def build():\n    print(cfg)\n    Daytona(cfg)\ndef print(value):\n    value.api_url = url\n",
        "def build():\n    json.dumps(cfg, default=str)\n    Daytona(cfg)\ndef str(value):\n    value.api_url = url\n",
        "def build():\n    Daytona(cfg)\ndef later():\n    global cfg\n    cfg.api_url = url\n",
        "alias = cfg\nDaytona(cfg)\n",
        "values = [cfg]\nDaytona(cfg)\n",
        "mutator = cfg.update\nDaytona(cfg)\n",
    ],
)
def test_config_refuses_when_use_can_mutate_or_escape(body: str) -> None:
    # Given later nested writes, unknown calls or shadowed reader names.
    source = (
        "import json, logging\nfrom daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        + body
    )
    # When preflighting config provenance.
    result = migrate(source)
    # Then no safe-reader spelling bypasses the escape fence.
    assert any(item["kind"] == "custom-api-url" for item in result.items)


@pytest.mark.parametrize(
    "body",
    [
        "def build(cfg):\n    return Daytona(cfg)\n",
        "Daytona(DaytonaConfig(api_url=url))\n",
        "Daytona(DaytonaConfig(**options))\n",
        "cfg = DaytonaConfig()\ncfg.api_url = url\nDaytona(cfg)\n",
    ],
)
def test_json_explains_cloud_assertion_when_config_is_unresolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    # Given a caller-supplied config and an old dependency pin.
    source = "from daytona import Daytona, DaytonaConfig\n" + body
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When requesting the real CLI report without an assertion.
    status = cli.main([str(tmp_path), "--force", "--yes", "--json"])
    report = json.loads(capsys.readouterr().out)
    # Then the atomic refusal distinguishes uncertainty from known custom URLs.
    assert status == 2
    assert (
        report["items"][0]["message"]
        == "cannot verify the API URL of this DaytonaConfig; if it targets Daytona Cloud, rerun with --drop-api-url"
    )
    assert (
        report["refused"]["message"]
        == "Cannot verify that every DaytonaConfig targets Daytona Cloud. If they do, rerun with --drop-api-url; for a custom API URL, stay on SDK 0.x. No files were written."
    )
    assert (
        "rerun with --drop-api-url if every DaytonaConfig targets Daytona Cloud"
        in report["nextSteps"]
    )
    assert (tmp_path / "app.py").read_text() == source
    assert (tmp_path / "requirements.txt").read_text() == "daytona==0.224.0\n"
