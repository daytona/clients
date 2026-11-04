# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bare doubles acquire client provenance only through all proven consumers."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate as migrate_source

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(body: str) -> SourceResult:
    source = (
        "from unittest.mock import MagicMock, Mock, AsyncMock, create_autospec\n"
        "from provider import Service, consume\n"
        "from daytona import Daytona, Resources\n"
        "async def test_snapshot(service: Service):\n" + body
    )
    mapping = load_mapping()
    provider = "from daytona import AsyncDaytona\nasync def consume(client: AsyncDaytona):\n    await client.create()\nclass Service:\n    async def create_with_retry(self, client: AsyncDaytona, name=None):\n        await client.snapshot.create(name)\n"
    index = build_index({"test_app": source, "provider": provider}, mapping)
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", index),
    )


@pytest.mark.parametrize(
    "factory",
    ["MagicMock()", "Mock()", "AsyncMock()", "create_autospec(Daytona, instance=True)"],
)
@pytest.mark.parametrize(
    "consumer",
    [
        "await service.create_with_retry(daytona, 'image')",
        "await service.create_with_retry(client=daytona)",
        "await consume(daytona)",
    ],
)
def test_routes_mock_setup_and_assertions_when_all_consumers_require_root_client(
    factory: str, consumer: str
) -> None:
    # Given Harbor's bare snapshot client passed to an indexed annotated method.
    body = f"    daytona = {factory}\n    daytona.snapshot.get = AsyncMock()\n    daytona.snapshot.delete = AsyncMock()\n    daytona.snapshot.create = AsyncMock()\n    daytona.create.return_value = result\n    {consumer}\n    daytona.snapshot.create.assert_awaited_once()\n    daytona.close.assert_not_called()\n    daytona.v2.snapshot.get()\n"
    # When all non-member flows certify a root-client role.
    result = migrate(body)
    # Then setup and assertions agree with the production namespace; root members stay.
    assert "daytona.v0.snapshot.get = AsyncMock()" in result.source
    assert "daytona.v0.snapshot.delete = AsyncMock()" in result.source
    assert "daytona.v0.snapshot.create = AsyncMock()" in result.source
    assert "daytona.v0.create.return_value = result" in result.source
    assert "daytona.v0.snapshot.create.assert_awaited_once()" in result.source
    assert "daytona.close.assert_not_called()" in result.source
    assert "daytona.v0.v0" not in result.source
    assert "daytona.v2.snapshot.get()" in result.source
    assert result.items == ()


@pytest.mark.parametrize(
    "extra",
    [
        "consume_unknown(daytona)",
        "alias = daytona",
        "return daytona",
        "getattr(daytona, field)",
        "daytona = foreign",
        "def later(): return daytona",
        "await service.unknown(daytona)",
        "from foreign import daytona",
        "def daytona(): pass",
    ],
)
def test_preserves_fixmes_when_any_client_flow_is_unproven(extra: str) -> None:
    # Given one certified consumer plus an unproven escape or rebinding.
    body = f"    daytona = MagicMock()\n    daytona.snapshot.get = AsyncMock()\n    await service.create_with_retry(daytona)\n    {extra}\n"
    # When not every use supports the same client role.
    result = migrate(body)
    # Then no guessed namespace is installed and the existing review remains.
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "v0"
        for node in ast.walk(ast.parse(result.source))
    )
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "factory",
    [
        "MagicMock(spec=Foreign)",
        "create_autospec(Foreign)",
        "foreign()",
        "MagicMock(**options)",
    ],
)
def test_preserves_mock_when_factory_does_not_prove_bare_double(factory: str) -> None:
    # Given a possibly non-client spec or opaque constructor configuration.
    body = f"    daytona = {factory}\n    await service.create_with_retry(daytona)\n    daytona.snapshot.create()\n"
    # When downstream annotations alone cannot establish the object's shape.
    result = migrate(body)
    # Then the existing FIXME survives without a speculative rewrite.
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "v0"
        for node in ast.walk(ast.parse(result.source))
    )
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "placeholder", ["object()", "SimpleNamespace()", "cast(Any, object())"]
)
def test_memberless_placeholder_needs_no_item(placeholder: str) -> None:
    # Given a placeholder with no members passed where a client is expected.
    source = (
        "from types import SimpleNamespace\nfrom typing import Any, cast\n"
        "from daytona import Daytona\n"
        "def use(client: Daytona) -> None:\n    client.create()\n"
        f"def test_it() -> None:\n    use({placeholder})\n"
    )
    # When migrating it.
    result = migrate_source(source)
    # Then nothing that worked before can break, so no review item is raised.
    assert ast.parse(result.source)
    assert "client.v0.create()" in result.source
    assert not any(item["kind"] == "unsupported-file" for item in result.items)
    assert not any(item["kind"] == "mock-target" for item in result.items)
