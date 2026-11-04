# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dotted patch strings resolve project exports, never local spelling guesses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    mapping = load_mapping()
    sources = {
        "app": "from daytona import Daytona, AsyncDaytona\nclient = Daytona()\nasync_client = AsyncDaytona()\nother = object()\n",
        "service": "from app import client as connection, Daytona as Client\n",
        "test_app": source,
    }
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", build_index(sources, mapping)),
    )


@pytest.mark.parametrize(
    "api",
    [
        ("from unittest.mock import patch", "patch"),
        ("from unittest.mock import patch as p", "p"),
        ("from unittest import mock", "mock.patch"),
        ("import unittest.mock", "unittest.mock.patch"),
        ("import unittest.mock as m", "m.patch"),
        ("", "mocker.patch"),
    ],
)
@pytest.mark.parametrize(
    "form",
    [
        "{call}\n",
        "@{call}\ndef test_work(mock):\n    pass\n",
        "with {call}:\n    pass\n",
        "mock = {call}.start()\n",
    ],
)
@pytest.mark.parametrize(
    "prefix", ["app.client", "app.async_client", "service.connection"]
)
def test_string_routes_when_project_export_proves_client(
    api: tuple[str, str], form: str, prefix: str
) -> None:
    # Given dotted runtime paths, independent of imports in the patching module.
    imports, callee = api
    source = f"{imports}\n" + form.format(call=f'{callee}("{prefix}.create")')
    # When resolving each path through the project index.
    result = migrate(source)
    # Then the literal target routes to the proven compatibility client.
    assert result.source == source.replace(f"{prefix}.create", f"{prefix}.v0.create")
    assert result.items == ()


@pytest.mark.parametrize("member", sorted(load_mapping().moved))
@pytest.mark.parametrize("quote", ["'", '"', "r'", "'''", '"""'])
def test_string_preserves_literal_style_when_target_is_keyword(
    member: str, quote: str
) -> None:
    # Given any moved member in a keyword target with original quote/prefix style.
    close = quote.removeprefix("r")
    source = f"from unittest.mock import patch\npatch(new=fake, target={quote}app.client.{member}{close})\n"
    # When migrating the string target.
    result = migrate(source)
    # Then only the dotted namespace changes.
    assert result.source == source.replace(
        f"app.client.{member}", f"app.client.v0.{member}"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "target",
    [
        "daytona.Daytona.create",
        "app.Daytona.create",
        "service.Client.create",
        "unknown_daytona.client.create",
    ],
)
def test_string_reports_when_unproven_prefix_has_daytona_identity(target: str) -> None:
    # Given class targets or unresolved paths carrying explicit Daytona identity.
    source = f"from unittest.mock import patch\npatch('{target}')\n"
    # When applying precision-first mock review.
    result = migrate(source)
    # Then the target stays unchanged and receives a single manual item.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "target",
    [
        "x.create",
        "app.other.create",
        "app.missing.create",
        "app.missing_daytona.get",
        "app.client.close",
        "app.client.v0.create",
        "app.client.v2.create",
        "not a valid path.create",
    ],
)
def test_string_stays_clean_when_unrelated_or_already_namespaced(target: str) -> None:
    # Given unrelated/unproven paths or previously migrated targets.
    source = f"from unittest.mock import patch\npatch('{target}')\n"
    # When rerunning the migration with the same project index.
    result = migrate(source)
    # Then arbitrary create methods do not produce review storms.
    assert result.source == source
    assert result.items == ()


def test_cli_migrates_when_string_and_multiple_share_proven_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a project with both patch target forms.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nclient = Daytona()\n"
    )
    test_file = tmp_path / "test_app.py"
    source = 'from unittest.mock import patch\nimport app\npatch("app.client.create")\npatch.multiple(app.client, get=fake)\n'
    test_file.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    # When driving the installed CLI's strict JSON surface.
    status = main([str(tmp_path), "--json", "--strict"])
    # Then both patch forms migrate without manual review.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert test_file.read_text() == source.replace(
        "app.client.create", "app.client.v0.create"
    ).replace("(app.client,", "(app.client.v0,")


@pytest.mark.parametrize(
    "setup,target,expected",
    [
        (
            "from daytona import Daytona\nclient = Daytona()\nclient = object()\n",
            "app.client.create",
            "app.client.create",
        ),
        (
            "from daytona import Daytona\nclient = Daytona() if condition else object()\n",
            "app.client.create",
            "app.client.create",
        ),
        ("import daytona as sdk\n", "app.sdk.create", "app.sdk.create"),
        (
            "from daytona import Daytona as Client\n",
            "app.Client.create",
            "app.Client.create",
        ),
        (
            "from daytona import Daytona\nclass Client(Daytona):\n    def create(self):\n        pass\nclient = Client()\n",
            "app.client.create",
            "app.client.create",
        ),
        (
            "from daytona import Daytona\nclass Client(Daytona):\n    pass\nclient = Client()\n",
            "app.client.create",
            "app.client.v0.create",
        ),
    ],
)
def test_string_respects_proof_when_project_binding_changes(
    setup: str, target: str, expected: str
) -> None:
    # Given final export proofs, including class aliases and subclass ownership.
    source = f"from unittest.mock import patch\npatch('{target}')\n"
    mapping = load_mapping()
    index = build_index({"app": setup, "test_app": source}, mapping)
    # When migrating against those exports rather than string spelling alone.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", index),
    )
    # Then only SDK-owned members of proven instances gain a namespace.
    assert result.source == source.replace(target, expected)
    assert [item["kind"] for item in result.items] == (
        ["mock-target"] if target in {"app.sdk.create", "app.Client.create"} else []
    )


@pytest.mark.parametrize(
    "module", ["daytona", "daytona_sdk", "daytona.helpers", "custom_daytona"]
)
@pytest.mark.parametrize("suffix", ["create", "Daytona.create", "client.create"])
def test_string_stays_local_when_daytona_named_module_is_project_owned(
    module: str, suffix: str
) -> None:
    # Given a first-party module whose identity resembles the SDK, including aliases.
    source = f"from unittest.mock import patch\npatch('{module}.{suffix}')\npatch('bridge.local.{suffix}')\n"
    mapping = load_mapping()
    index = build_index(
        {
            module: "class Daytona: pass\nclient = Daytona()\n",
            "bridge": f"import {module} as local\n",
            "test_app": source,
        },
        mapping,
    )
    # When resolving direct and re-exported paths through project ownership.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", index),
    )
    # Then a Daytona-like name alone neither migrates nor reports first-party code.
    assert result.source == source
    assert result.items == ()
