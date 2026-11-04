# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor patches must never silently pass strict migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str, app: str) -> SourceResult:
    mapping = load_mapping()
    index = build_index({"app": app, "test_app": source}, mapping)
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", index),
    )


@pytest.mark.parametrize(
    "api",
    [
        ("from unittest.mock import patch", "patch"),
        ("from unittest.mock import patch as p", "p"),
        ("from unittest import mock", "mock.patch"),
        ("import unittest.mock", "unittest.mock.patch"),
        ("import unittest.mock as m", "m.patch"),
        ("import mock", "mock.patch"),
        ("from mock import patch", "patch"),
        ("", "mocker.patch"),
    ],
)
@pytest.mark.parametrize(
    "form",
    [
        "@{call}\ndef test_work(mock_client):\n    pass\n",
        "def test_work(mocker):\n    with {call} as mock_client:\n        pass\n",
        "def test_work(mocker):\n    mock_client = {call}.start()\n",
    ],
)
@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize("owner", ["app", "daytona", "daytona_sdk"])
def test_constructor_reports_when_string_resolves_to_sdk_class(
    api: tuple[str, str], form: str, client: str, owner: str
) -> None:
    # Given a root class imported into a project module or patched in the SDK.
    imports, callee = api
    source = imports + "\n" + form.format(call=f'{callee}("{owner}.{client}")')
    # When migrating each spelling and invocation form.
    result = migrate(source, f"from daytona import {client}\n")
    # Then exactly one manual item prevents silently accepting the old mock shape.
    assert [item["kind"] for item in result.items] == ["mock-target"]
    assert result.source == source.replace(
        f'"daytona_sdk.{client}"', f'"daytona.{client}"'
    )


@pytest.mark.parametrize("target", ["app.Client", "app.sdk.Daytona"])
def test_constructor_reports_when_class_is_reexported_under_alias(target: str) -> None:
    # Given project aliases of a root constructor.
    source = f"from unittest.mock import patch\npatch('{target}')\n"
    # When resolving the full dotted path.
    result = migrate(
        source, "from daytona import Daytona as Client\nimport daytona as sdk\n"
    )
    # Then aliases have the same manual-review contract as the public name.
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("module", ["daytona", "daytona_sdk"])
def test_constructor_stays_local_when_sdk_named_module_is_project_owned(
    module: str,
) -> None:
    # Given a user-owned module, not the SDK constructor.
    source = f"from unittest.mock import patch\npatch('{module}.Daytona')\n"
    mapping = load_mapping()
    index = build_index({module: "class Daytona: pass\n", "test_app": source}, mapping)
    # When resolving its constructor patch.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", index),
    )
    # Then project ownership exempts both reporting and rewriting.
    assert result.source == source
    assert result.items == ()


def test_cli_strict_fails_when_constructor_mock_needs_new_return_value_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given the reviewed application and its constructor-mocking test.
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\ndef run():\n    return Daytona().create()\n"
    )
    test_file = tmp_path / "test_app.py"
    source = 'from unittest.mock import patch\nimport app\n@patch("app.Daytona")\ndef test_work(mock_client):\n    mock_client.return_value.create.return_value = "sandbox"\n    assert app.run() == "sandbox"\n'
    test_file.write_text(source)
    # When running the strict CLI through its JSON surface.
    status = main([str(tmp_path), "--json", "--strict"])
    # Then the unsafe mock is reported instead of silently passing strict mode.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["mock-target"]
    assert test_file.read_text() == source
