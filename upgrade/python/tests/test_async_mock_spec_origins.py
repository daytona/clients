# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Async mock specs retain their origins through packages and factory annotations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("initializer", ["__init__.py", "__init__.pyi", "__init__.pyw"])
@pytest.mark.parametrize(
    "binding", ["from .provider import Client", "from .provider import *"]
)
def test_requires_review_when_async_spec_is_reexported_by_package(
    initializer: str, binding: str
) -> None:
    # Given a package-relative SDK re-export whose filename establishes its scope.
    mapping = load_mapping()
    source = (
        "from unittest.mock import MagicMock\nfrom package import Client\n"
        "client = MagicMock(spec=Client)\n"
        "async def run():\n    return await client.create()\n"
    )
    index = build_index(
        {
            "package": binding + "\n",
            "package.provider": "from daytona import AsyncDaytona as Client\n",
            "app": source,
        },
        mapping,
        {"package": "package/" + initializer},
    )

    # When the mock spec crosses the same import edge as its client-class proof.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )

    # Then the await stays untouched and cannot silently target a sync property mock.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "annotation",
    [
        "type[AsyncDaytona]",
        "Type[AsyncDaytona]",
        '"type[AsyncDaytona]"',
        'type["AsyncDaytona"]',
        "type[sdk.AsyncDaytona]",
        "Annotated[type[AsyncDaytona], 'metadata']",
    ],
)
def test_requires_review_when_factory_return_annotation_names_async_spec(
    annotation: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a class-returning factory whose shared proof deliberately erases asyncness.
    source = (
        "from unittest.mock import MagicMock\n"
        "from typing import Type, Annotated\n"
        "from daytona import AsyncDaytona\nimport daytona as sdk\n"
        f"def make_cls() -> {annotation}:\n    return AsyncDaytona\n"
        "client = MagicMock(spec=make_cls())\n"
        "client.create.return_value = 'sandbox'\n"
        "async def run():\n    return await client.create()\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When the public CLI applies the mock-only async origin guard.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then strict review fails without rewriting the configured mock or awaited call.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert [item["kind"] for item in report["items"]] == ["mock-target"]


@pytest.mark.parametrize(
    "body",
    [
        "client = MagicMock(spec=make_cls())\nclient.create()\n",
        "def run():\n    client = MagicMock(spec=make_cls())\n    client.create()\n",
    ],
)
@pytest.mark.parametrize(
    "annotation",
    ["type[Daytona]", '"type[Daytona]"', "Annotated[type[Daytona], AsyncDaytona]"],
)
def test_routes_sync_mock_when_factory_annotation_is_synchronous(
    body: str, annotation: str
) -> None:
    # Given an unrelated async import and a factory annotated with the sync class.
    source = (
        "from unittest.mock import MagicMock\n"
        "from typing import Annotated\n"
        "from daytona import Daytona, AsyncDaytona\n"
        f"def make_cls() -> {annotation}:\n    return Daytona\n" + body
    )
    mapping = load_mapping()
    index = build_index({"app": source}, mapping)

    # When sync and async class annotations keep their existing shared proof identity.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )

    # Then synchronous mock routing remains automatic, not demoted by the async guard.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("location", ["local", "imported", "package"])
def test_requires_review_when_mock_factory_is_defined_after_consumer(
    location: str,
) -> None:
    # Given deferred code that uses a factory bound later during module execution.
    factory = (
        "from daytona import AsyncDaytona\n"
        "def make_cls() -> type[AsyncDaytona]:\n    return AsyncDaytona\n"
    )
    source = (
        "from unittest.mock import MagicMock\n"
        "def run():\n    client = MagicMock(spec=make_cls())\n"
        "    return client.create()\n"
    )
    projects: dict[str, tuple[str, dict[str, str]]] = {
        "local": (factory, {}),
        "imported": ("from provider import make_cls\n", {"provider": factory}),
        "package": (
            "from package import make_cls\n",
            {
                "package": "from .provider import make_cls\n",
                "package.provider": factory,
            },
        ),
    }
    suffix, sources = projects[location]
    source += suffix
    sources["app"] = source
    mapping = load_mapping()
    index = build_index(sources, mapping, {"package": "package/__init__.py"})

    # When fixed-point indexing supplies forward factory summaries to the consumer.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )

    # Then imported and local summaries both retain the async-spec review guard.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
