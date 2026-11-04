# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Async-spec review resolves SDK import names without changing client proofs."""

from __future__ import annotations

import pytest

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
    "setup,spec",
    [
        ("import daytona as sdk\n", "sdk.AsyncDaytona"),
        ("import daytona_sdk as sdk\n", "sdk.AsyncDaytona"),
        (
            "from daytona_sdk import AsyncDaytona as Original\nClient = Original\n",
            "Client",
        ),
        ("import daytona as sdk\nClient = sdk.AsyncDaytona\n", "Client"),
        ("from daytona import AsyncDaytona\nspec = AsyncDaytona()\n", "spec"),
        ("from daytona import AsyncDaytona\n", "AsyncDaytona"),
        ("from daytona import *\n", "AsyncDaytona"),
    ],
)
def test_reviews_async_spec_when_original_sdk_import_is_aliased(
    setup: str, spec: str
) -> None:
    # Given SDK class or instance aliases, including the legacy module spelling.
    source = (
        "from unittest.mock import MagicMock\n"
        f"{setup}client = MagicMock(spec={spec})\nclient.create()\n"
    )
    # When the spec expression is resolved independently of ordinary client identity.
    result = migrate(source)
    # Then async mock calls stay manual, even when the import name is hidden.
    assert "client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "setup",
    [
        "from daytona import AsyncDaytona as Client, Daytona\nClient = Daytona\n",
        "from daytona import AsyncDaytona as Original, Daytona\nClient = Original\nClient = Daytona\n",
        "import daytona as sdk\nsdk.AsyncDaytona = sdk.Daytona\nClient = sdk.AsyncDaytona\n",
    ],
)
def test_routes_sync_spec_when_async_alias_has_been_rebound(setup: str) -> None:
    # Given an old async spelling whose actual value is now the synchronous class.
    source = (
        "from unittest.mock import MagicMock\n"
        f"{setup}client = MagicMock(spec=Client)\nclient.create()\n"
    )
    # When migration uses current bindings rather than stale import spellings.
    result = migrate(source)
    # Then the synchronous mock retains its existing automatic routing behavior.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


def test_reviews_async_spec_when_project_reexports_sdk_alias() -> None:
    # Given a project module re-exporting an asynchronous SDK class.
    mapping = load_mapping()
    source = (
        "from unittest.mock import create_autospec\nfrom provider import Client\n"
        "client = create_autospec(Client, instance=True)\nclient.create()\n"
    )
    index = build_index(
        {"provider": "from daytona import AsyncDaytona as Client\n", "app": source},
        mapping,
    )
    # When the consumer resolves the spec through the project import index.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )
    # Then the re-export does not turn the async class into a synchronous spec.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
