# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compound mock specs and colliding configuration require manual review."""

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
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize(
    "spec",
    [
        "AsyncDaytona if flag else Daytona",
        "Daytona if flag else AsyncDaytona",
        "Daytona if flag else unknown",
        "unknown if flag else Daytona",
        "Daytona if flag else (AsyncDaytona if other else Daytona)",
        "(Client := AsyncDaytona)",
        "(Client := sdk.AsyncDaytona)",
        "(Client := AsyncDaytona) if flag else Daytona",
    ],
)
def test_reviews_mock_when_spec_can_be_async_or_unresolved(spec: str) -> None:
    # Given a spec whose synchronous mock shape is not guaranteed.
    source = (
        "from unittest.mock import MagicMock\n"
        "from daytona import Daytona, AsyncDaytona\nimport daytona as sdk\n"
        f"client = MagicMock(spec={spec})\nclient.create()\n"
    )
    # When the upgrader considers namespace routing.
    result = migrate(source)
    # Then it preserves the calls and requires explicit mock setup review.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_reviews_alias_when_async_class_was_bound_inside_spec() -> None:
    # Given a second mock using the class alias established by a walrus spec.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import AsyncDaytona\n"
        "first = MagicMock(spec=(Client := AsyncDaytona))\n"
        "second = MagicMock(spec=Client)\nsecond.create()\n"
    )
    # When the bound class is reused as a spec.
    result = migrate(source)
    # Then both constructions require review and neither certifies routing.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target", "mock-target"]


def test_routes_mock_when_both_conditional_specs_are_synchronous() -> None:
    # Given two equivalent synchronous client class alternatives.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "Sync = Daytona\nclient = MagicMock(spec=Daytona if flag else Sync)\n"
        "client.create()\n"
    )
    # When both branches establish a synchronous spec.
    result = migrate(source)
    # Then the existing safe namespace routing remains available.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "configuration",
    [
        '**{"create.return_value": 1, "v0.create.return_value": 2}',
        '**{"v0.create.return_value": 2, "create.return_value": 1}',
        '**{"create.return_value": 1}, **{"v0.create.return_value": 2}',
        'create=first, **{"v0.create": second}',
        '**{"v0.create": second}, create=first',
        '**{"snapshot.get.return_value": 1, "v0.snapshot.get.return_value": 2}',
        '**{"create.return_value": 1, "v0." "create.return_value": 2}',
    ],
)
@pytest.mark.parametrize("receiver", ["client", "alias"])
def test_fences_mock_when_configuration_keys_collide(
    configuration: str, receiver: str
) -> None:
    # Given keys that become duplicates only after namespace routing.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\nalias = client\n"
        f"{receiver}.configure_mock({configuration})\n"
        "client.create()\nalias.create()\n"
    )
    # When the upgrader normalizes every key across the entire call.
    result = migrate(source)
    # Then neither configuration nor any possible alias is rewritten.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_strict_refuses_when_configuration_keys_collide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a project whose rewrite would silently overwrite a configured value.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\n"
        'client.configure_mock(**{"create": first, "v0.create": second})\n'
        "client.create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "app.py").write_text(source)
    # When the public CLI applies strict review.
    status = main([str(tmp_path), "--strict", "--json"])
    # Then manual review blocks success without writing source changes.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert [item["kind"] for item in report["items"]] == ["mock-target"]


@pytest.mark.parametrize(
    "receiver", ["constructor.return_value", "constructor()", "client"]
)
def test_fences_constructor_result_when_configuration_keys_collide(
    receiver: str,
) -> None:
    # Given colliding setup on an autospec constructor's shared default instance.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\nclient = constructor()\n"
        f'{receiver}.configure_mock(**{{"create": first, "v0.create": second}})\n'
        "client.create()\nconstructor.return_value.create()\n"
        "later = constructor()\nlater.create()\n"
    )
    # When normalization makes the configuration ambiguous.
    result = migrate(source)
    # Then no path to that shared result keeps an automatic rewrite proof.
    assert result.source == source
    assert any(item["kind"] == "mock-target" for item in result.items)
