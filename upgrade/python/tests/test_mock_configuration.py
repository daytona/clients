# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mock configuration follows namespace routing or blocks unsafe proof reuse."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize(
    "construction",
    [
        "MagicMock(spec=Client)",
        "AsyncMock(spec=Client)",
        "create_autospec(Client)",
        "create_autospec(Client, instance=True)",
        "MagicMock(spec_set=Client)",
        "MagicMock(spec=Client())",
        "AsyncMock(spec=Client())",
        "create_autospec(Client())",
        "MagicMock(Client)",
        "Mock(spec=Client)",
        "create_autospec(spec=Client, instance=True)",
        "m.MagicMock(spec=Client)",
        "autospec(Client)",
    ],
)
def test_requires_review_when_mock_spec_is_async_client(
    construction: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an async SDK spec, including aliases and instance forms.
    source = (
        "from unittest.mock import MagicMock, Mock, AsyncMock, create_autospec\n"
        "from unittest.mock import create_autospec as autospec\n"
        "import unittest.mock as m\n"
        "from daytona import AsyncDaytona as Client\n"
        f"client = {construction}\n"
        "async def run():\n    return await client.create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "app.py").write_text(source)
    # When strict migration evaluates the mock's property-backed namespace.
    status = main([str(tmp_path), "--strict", "--json"])
    # Then calls stay unchanged and the unsafe mock cannot pass strict review.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert [item["kind"] for item in report["items"]] == ["mock-target"]


@pytest.mark.parametrize(
    "configuration,expected,call",
    [
        (
            '**{"create.return_value": 42}',
            '**{"v0.create.return_value": 42}',
            "create()",
        ),
        ("create=replacement", '**{"v0.create": replacement}', "create()"),
        (
            '**{"snapshot.get.return_value": 42}',
            '**{"v0.snapshot.get.return_value": 42}',
            "snapshot.get('image')",
        ),
    ],
)
def test_routes_configuration_when_moved_member_key_is_literal(
    configuration: str, expected: str, call: str
) -> None:
    # Given method configuration on a proven synchronous instance mock.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "replacement = MagicMock(return_value=42)\nclient = MagicMock(spec=Daytona)\n"
        f"client.configure_mock({configuration})\nassert client.{call} == 42\n"
    )
    # When both setup and use are migrated.
    result = migrate(source)
    # Then the configuration reaches the exact member used by the rewritten call.
    assert result.source == source.replace(configuration, expected).replace(
        f"client.{call}", f"client.v0.{call}"
    )
    assert result.items == ()


@pytest.mark.parametrize("configuration", ["**config", "**{key: 42}", "**{**config}"])
@pytest.mark.parametrize("receiver", ["client", "alias"])
def test_requires_review_when_configuration_keys_are_dynamic(
    configuration: str, receiver: str
) -> None:
    # Given unknown configuration and a possible alias of a proven instance mock.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\nalias = client\n"
        f"{receiver}.configure_mock({configuration})\nclient.create()\nalias.create()\n"
    )
    # When migrating configuration that could replace any client member.
    result = migrate(source)
    # Then neither alias retains rewrite proof and the configuration needs review.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_preserves_configuration_when_members_stay_on_root() -> None:
    # Given root namespaces and close, plus an already migrated method key.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\n"
        'client.configure_mock(v0=first, v2=second, close=third, **{"v0.create.return_value": 42})\n'
        "client.close()\nclient.v0.create()\n"
    )
    # When migrating already valid root configuration.
    result = migrate(source)
    # Then root members and qualified paths stay untouched.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "receiver", ["constructor.return_value", "constructor()", "client"]
)
def test_fences_constructor_result_when_instance_configuration_is_dynamic(
    receiver: str,
) -> None:
    # Given an autospec constructor and its default instance, possibly aliased.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\nclient = constructor()\n"
        f"{receiver}.configure_mock(**config)\n"
        "client.create()\nconstructor.return_value.create()\n"
        "later = constructor()\nlater.create()\n"
    )
    # When opaque instance configuration can affect the constructor's shared result.
    result = migrate(source)
    # Then no path to that result is automatically routed after the mutation.
    assert result.source == source
    assert any(item["kind"] == "mock-target" for item in result.items)


@pytest.mark.parametrize("configuration", ["**config", "**{key: 42}", "**{**config}"])
def test_strict_fails_when_configuration_keys_are_dynamic(
    configuration: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given configuration the upgrader cannot inspect safely.
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "app.py").write_text(
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\n"
        f"client.configure_mock({configuration})\nclient.create()\n"
    )
    # When strict review drives the public CLI.
    status = main([str(tmp_path), "--strict", "--json"])
    # Then unresolved setup cannot succeed or silently reroute the later call.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert [item["kind"] for item in report["items"]] == ["mock-target"]


@pytest.mark.parametrize(
    "construction",
    [
        "MagicMock(spec=AsyncDaytona)",
        "AsyncMock(spec=AsyncDaytona)",
        "create_autospec(AsyncDaytona)",
        "create_autospec(AsyncDaytona, instance=True)",
    ],
)
def test_requires_manual_async_setup_when_using_built_sdk(construction: str) -> None:
    # Given the real root SDK class and the four previously rewritten mock forms.
    interpreter = os.environ.get("DAYTONA_UPGRADE_TEST_SDK_PYTHON")
    if interpreter is None:
        pytest.skip("set DAYTONA_UPGRADE_TEST_SDK_PYTHON to a bundled SDK interpreter")
    check = (
        "from unittest.mock import MagicMock, AsyncMock, create_autospec\n"
        "from inspect import isawaitable\nfrom daytona import AsyncDaytona\n"
        f"client = {construction}\n"
        "assert isinstance(AsyncDaytona.v0, property)\n"
        "assert not isawaitable(client.v0.create())\n"
    )
    # When inspecting the actual result of the naive namespace migration.
    completed = subprocess.run(
        [interpreter, "-I", "-c", check],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the real property-backed mock confirms that rewriting await is unsafe.
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "configuration,call",
    [
        ('**{"create.return_value": 42}', "create()"),
        ("create=replacement", "create()"),
        ('**{"snapshot.get.return_value": 42}', "snapshot.get('image')"),
    ],
)
def test_runs_configuration_when_using_built_sdk(configuration: str, call: str) -> None:
    # Given the real built SDK, whose root has no moved members.
    interpreter = os.environ.get("DAYTONA_UPGRADE_TEST_SDK_PYTHON")
    if interpreter is None:
        pytest.skip("set DAYTONA_UPGRADE_TEST_SDK_PYTHON to a bundled SDK interpreter")
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "replacement = MagicMock(return_value=42)\nclient = MagicMock(spec=Daytona)\n"
        f"client.configure_mock({configuration})\nassert client.{call} == 42\n"
    )
    migrated = migrate(source)
    # When executing the migrated program through the installed SDK surface.
    completed = subprocess.run(
        [interpreter, "-I", "-c", migrated.source],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the configured return value is observed without root-member errors.
    assert completed.returncode == 0, completed.stderr
