# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL cleanup must not destroy config values observed by other consumers."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize("field", ["api_url", "server_url"])
@pytest.mark.parametrize(
    "reader",
    [
        'print(config.api_url + "/health")',
        "print(config)",
        "consume(config)",
        "saved = [config]",
        "alias = config",
        "def read():\n    return config",
        "def read():\n    return config.api_url",
    ],
)
def test_retains_url_when_config_has_other_consumers(field: str, reader: str) -> None:
    # Given a Cloud config shared with a client and a separate observer.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f'config = DaytonaConfig(api_key=k, {field}="https://app.daytona.io/api")\n'
        "d = Daytona(config)\n" + reader.replace("api_url", field) + "\n"
    )

    # When explicit Cloud assertion permits migration even for escaping configs.
    result = migrate(source, drop=True)

    # Then URL data is untouched and review is attached to the config constructor.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(
        item["kind"] == "config-field" and item["line"] == 2 for item in result.items
    )
    assert "FIXME(daytona-upgrade)" in result.source


@pytest.mark.parametrize(
    "reader",
    [
        "from app import config\nprint(config.api_url)\n",
        "import app\nprint(app.config.api_url)\n",
        "from app import *\nprint(config.api_url)\n",
        "from bridge import config\nprint(config.api_url)\n",
    ],
)
def test_retains_url_when_another_module_reads_exported_config(
    tmp_path: Path, reader: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a module-level config whose URL is read across an import edge.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        'config = DaytonaConfig(api_url="https://app.daytona.io/api")\n'
        "d = Daytona(config)\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "reader.py").write_text(reader)
    (tmp_path / "bridge.py").write_text("from app import config\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When project preflight indexes other modules' read-only uses.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then it preserves the URL while making manual client-config separation required.
    report = json.loads(capsys.readouterr().out)
    output = (tmp_path / "app.py").read_text()
    assert status == 1
    assert ast.dump(ast.parse(output)) == ast.dump(ast.parse(source))
    assert any(
        item["kind"] == "config-field" and item["file"] == "app.py"
        for item in report["items"]
    )
    assert "FIXME(daytona-upgrade)" in output


@pytest.mark.parametrize("argument", ["config", "config=config"])
def test_drops_url_when_only_consumer_is_client_constructor(argument: str) -> None:
    # Given an exclusive config whose URL is not part of another consumer's data.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        'config = DaytonaConfig(api_url="https://app.daytona.io/api")\n'
        f"d = Daytona({argument})\n"
    )

    # When the ordinary migration removes root-incompatible URL fields.
    result = migrate(source)

    # Then the safe existing automatic cleanup remains available.
    assert "DaytonaConfig()" in result.source
    assert result.items == ()


def test_preserves_shared_cloud_url_without_requiring_assertion() -> None:
    # Given a proven Cloud config observed through a scalar attribute.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        'config = DaytonaConfig(api_url="https://app.daytona.io/api")\n'
        'd = Daytona(config)\nprint(config.api_url + "/health")\n'
    )

    # When no custom API assertion is supplied.
    result = migrate(source)

    # Then this known Cloud URL needs config separation, not endpoint refusal.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["config-field"]


def test_drops_url_when_other_reader_has_an_unrelated_lexical_binding() -> None:
    # Given same-spelled readers whose parameter does not reference the config.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        'config = DaytonaConfig(api_url="https://app.daytona.io/api")\n'
        "def read(config):\n    return config.api_url\n"
        "d = Daytona(config)\n"
    )

    # When URL cleanup resolves lexical ownership rather than just spelling.
    result = migrate(source)

    # Then the unrelated reader does not stop exclusive-client cleanup.
    assert "DaytonaConfig()" in result.source
    assert result.items == ()


def test_retains_shared_url_and_review_on_second_migration(tmp_path: Path) -> None:
    # Given a shared config that already carries the first migration's FIXME.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        'config = DaytonaConfig(api_url="https://app.daytona.io/api")\n'
        "d = Daytona(config)\nprint(config.api_url)\n"
    )
    migrated = migrate(source).source
    app = tmp_path / "app.py"
    app.write_text(migrated)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When the actual strict CLI revisits an unresolved manual config separation.
    status = main([str(tmp_path), "--force", "--yes", "--strict"])

    # Then the warning remains actionable without duplicate comments or URL loss.
    assert status == 1
    assert app.read_text() == migrated
