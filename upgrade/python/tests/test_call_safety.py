# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Config splats and imported call aliases retain the preflight contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str, *, drop: bool = False) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, drop_api_url=drop),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize(
    "config,drop",
    [
        ("cfg", False),
        ("{'api_url': 'https://private.example/api'}", False),
        ("{'api_url': 'https://private.example/api'}", True),
        ("{**{'api_url': 'https://private.example/api'}}", False),
        ("{**{'api_url': 'https://private.example/api'}}", True),
        ("{'api_url': 'https://private.example/api', **cfg}", True),
    ],
    ids=[
        "url-splat",
        "url-inline-splat",
        "custom-drop",
        "nested-literal",
        "nested-drop",
        "custom-unresolved-drop",
    ],
)
def test_project_refuses_before_writes_when_config_splat_is_unsafe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], config: str, drop: bool
) -> None:
    # Given an earlier editable file and dependency plus a later unsafe config.
    files = {
        "a.py": "from daytona import Daytona\nd = Daytona()\nd.create()\n",
        "requirements.txt": "daytona==0.224.0\n",
        "z.py": "from daytona import DaytonaConfig\nurl = 'https://private.example/api'\ncfg = {'api_url': url}\nc = DaytonaConfig(**"
        + config
        + ")\n",
    }
    for name, source in files.items():
        (tmp_path / name).write_text(source)
    # When the real CLI performs its project-wide preflight.
    status = main([str(tmp_path), "--json", *(["--drop-api-url"] if drop else [])])
    # Then no source or dependency is written, even under the explicit drop flag.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "config",
    ["cfg", "{key: value}", "{**cfg}", "{'api_key': token, **cfg}"],
)
@pytest.mark.parametrize("drop", [False, True])
def test_config_stays_user_owned_when_splat_has_unresolved_keys(
    config: str, drop: bool
) -> None:
    # Given keys that cannot establish whether the mapping contains api_url.
    source = f"from daytona import DaytonaConfig\nc = DaytonaConfig(api_url='https://app.daytona.io/api', **{config})\n"
    # When the tool checks the call, optionally with the user's URL assertion.
    result = migrate(source, drop=drop)
    # Then the whole call remains untouched and uncertainty is explicit.
    assert result.source == source
    assert [item["kind"] for item in result.items] == [
        "config-field" if drop else "custom-api-url"
    ]


@pytest.mark.parametrize(
    "config,expected",
    [
        ("{'api_url': 'https://app.daytona.io/api/'}", "{}"),
        ("{'api_url': url}", "{}"),
        (
            "{'api_key' : token, 'api_url': 'https://app.daytona.io/api'}",
            "{'api_key' : token, }",
        ),
        ("{**{'api_url': 'https://app.daytona.io/api'}}", "{**{}}"),
        ("{'api_key': token}", "{'api_key': token}"),
    ],
)
def test_literal_splat_uses_explicit_field_rules_when_keys_are_known(
    config: str, expected: str
) -> None:
    # Given a literal mapping whose keys and removable cloud URL are known.
    source = f"from daytona import DaytonaConfig\nc = DaytonaConfig(**{config})\n"
    # When applying field rules, including the explicit dynamic URL assertion.
    result = migrate(source, drop=True)
    # Then only the URL entry changes; retained field formatting is preserved.
    assert result.source == source.replace(config, expected)
    assert [item["kind"] for item in result.items] == (
        ["config-field"] if config == "{'api_url': url}" else []
    )


def test_field_diagnostics_when_literal_splat_contains_removed_fields() -> None:
    # Given deprecated and unsupported keys carried through a literal splat.
    source = "from daytona import DaytonaConfig\nc = DaytonaConfig(**{'target': target, '_experimental': feature})\n"
    # When inspecting configuration keys.
    result = migrate(source)
    # Then the same per-field diagnostics apply as for explicit keywords.
    assert [item["kind"] for item in result.items] == [
        "deprecated-target",
        "config-field",
    ]
    assert "**{'target': target, '_experimental': feature}" in result.source


def test_formatting_when_cloud_url_is_removed_from_multiline_splat() -> None:
    # Given multiline formatting and a comment belonging to a retained field.
    source = (
        "from daytona import DaytonaConfig\n"
        "c = DaytonaConfig(**{\n"
        "    'api_key' : token,  # keep this comment\n"
        "    'api_url': 'https://app.daytona.io/api/',\n"
        "    'organization_id': org,\n"
        "})\n"
    )
    # When migrating the literal mapping without a drop assertion.
    result = migrate(source)
    # Then the cloud URL entry alone disappears, retaining surrounding trivia.
    assert result.source == source.replace(
        "    'api_url': 'https://app.daytona.io/api/',\n", ""
    )
    assert result.items == ()


def test_project_upgrades_when_user_asserts_unresolved_splat_is_cloud_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an unresolved config which the user will migrate themselves.
    source = "from daytona import DaytonaConfig\nc = DaytonaConfig(**cfg)\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.224.0\n")
    # When the explicit assertion permits dependency migration.
    status = main([str(tmp_path), "--json", "--drop-api-url"])
    # Then the config stays byte-exact and is reported for manual migration.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert app.read_text() == source
    assert requirements.read_text() == "daytona[v0-sdk]>=2,<3\n"
    assert [item["kind"] for item in report["items"]] == ["config-field"]


@pytest.mark.parametrize(
    "imports,operation,expected,kinds",
    [
        (
            "from unittest.mock import patch as p\nfrom importlib import import_module as load\n",
            "p('daytona.Sandbox.delete'); load('daytona_sdk')",
            "p('daytona.v0.Sandbox.delete'); load('daytona')",
            ["dynamic-import"],
        ),
        (
            "from unittest.mock import patch as p\n",
            "p.object(client, 'create')",
            "p.object(client, 'create')",
            [],
        ),
        (
            "import unittest.mock as m\n",
            "m.patch('daytona.Sandbox.delete')",
            "m.patch('daytona.v0.Sandbox.delete')",
            [],
        ),
        (
            "import importlib as il\n",
            "il.import_module('daytona_sdk')",
            "il.import_module('daytona')",
            ["dynamic-import"],
        ),
        (
            "from importlib import import_module as load\n",
            "load(module_name)",
            "load(module_name)",
            ["dynamic-import"],
        ),
    ],
)
def test_alias_call_when_imported_binding_proves_callee(
    imports: str, operation: str, expected: str, kinds: list[str]
) -> None:
    # Given aliased SDK and mock imports.
    source = imports + "import daytona\n" + operation + "\n"
    # When matching the callee's canonical imported binding.
    result = migrate(source)
    # Then the target is migrated or reported without changing alias spellings.
    assert result.source == imports + "import daytona\n" + expected + "\n"
    assert [item["kind"] for item in result.items] == kinds
