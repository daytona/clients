# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only positively synchronous class specs can certify mock namespace edits."""

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


@pytest.mark.parametrize("client_type", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize(
    "factory,spec",
    [
        (
            "class Factory:\n    @staticmethod\n    def select() -> type[CLIENT]:\n        return CLIENT\n",
            "Factory.select()",
        ),
        (
            "class Factory:\n    def select(self) -> type[CLIENT]:\n        return CLIENT\n",
            "Factory().select()",
        ),
        (
            "class Factory:\n    @classmethod\n    def select(cls) -> type[CLIENT]:\n        return CLIENT\n",
            "Factory.select()",
        ),
        ("def select() -> type[CLIENT]:\n    return CLIENT\n", "select()"),
    ],
)
@pytest.mark.parametrize("postponed", [False, True])
def test_routes_only_sync_specs_when_factory_annotations_are_resolved(
    client_type: str, factory: str, spec: str, postponed: bool
) -> None:
    # Given method factories or postponed annotations declared before SDK imports.
    definition = factory.replace("CLIENT", client_type)
    sdk_import = f"from daytona import {client_type}\n"
    source = (
        ("from __future__ import annotations\n" if postponed else "")
        + "from unittest.mock import MagicMock\n"
        + (definition + sdk_import if postponed else sdk_import + definition)
        + f"client = MagicMock(spec={spec})\n"
        + "client.create.return_value = 'sandbox'\n"
        + "async def run():\n    return await client.create()\n"
    )

    # When the mock's class origin is resolved separately from its client type.
    result = migrate(source)

    # Then only the synchronous class establishes a safe v0 mock shape.
    if client_type == "Daytona":
        assert result.source == source.replace("client.create", "client.v0.create")
        assert result.items == ()
    else:
        assert result.source == source
        assert [item["kind"] for item in result.items] == ["mock-target"]


def test_requires_review_when_client_class_origin_is_not_proven() -> None:
    # Given a client-class-typed parameter, which may be AsyncDaytona at runtime.
    source = (
        "from daytona import Daytona\nfrom unittest.mock import MagicMock\n"
        "def run(selected: type[Daytona]):\n"
        "    client = MagicMock(spec=selected)\n    return client.create()\n"
    )

    # When a shared client proof lacks positive synchronous class provenance.
    result = migrate(source)

    # Then the unproven receiver is never rewritten automatically.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("replacement", ["sdk.AsyncDaytona", "unknown"])
def test_requires_review_when_namespace_class_has_been_rebound(
    replacement: str,
) -> None:
    # Given an SDK spelling whose current class origin no longer proves syncness.
    source = (
        "import daytona as sdk\nfrom unittest.mock import MagicMock\n"
        f"sdk.Daytona = {replacement}\n"
        "client = MagicMock(spec=sdk.Daytona)\nclient.create()\n"
    )

    # When the spec is read after the namespace write.
    result = migrate(source)

    # Then an original import spelling cannot resurrect synchronous provenance.
    assert result.source == source
    assert "client.v0.create" not in result.source


def test_routes_sync_mock_when_imported_factory_is_aliased() -> None:
    # Given an imported factory with a positively synchronous class annotation.
    mapping = load_mapping()
    source = (
        "from provider import select as original\n"
        "from unittest.mock import MagicMock\nfactory = original\n"
        "client = MagicMock(spec=factory())\nclient.create()\n"
    )
    index = build_index(
        {
            "provider": "from daytona import Daytona\ndef select() -> type[Daytona]:\n    return Daytona\n",
            "app": source,
        },
        mapping,
    )

    # When factory metadata crosses imports and a local callable alias.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )

    # Then aliases preserve proof without falling back to the erased client type.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


def test_requires_review_when_instance_replaces_sync_class_factory() -> None:
    # Given a sync method replaced by an async-class factory in the initializer.
    source = (
        "from daytona import Daytona, AsyncDaytona\nfrom unittest.mock import MagicMock\n"
        "def replacement() -> type[AsyncDaytona]:\n    return AsyncDaytona\n"
        "class Factory:\n"
        "    def __init__(self):\n        self.select = replacement\n"
        "    def select(self) -> type[Daytona]:\n        return Daytona\n"
        "client = MagicMock(spec=Factory().select())\nclient.create()\n"
    )

    # When identical shared result types hide contradictory class origins.
    result = migrate(source)

    # Then the overwritten method's original sync marker cannot certify a rewrite.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_requires_review_when_class_scope_shadows_synchronous_annotation() -> None:
    # Given a type spelling that resolves to AsyncDaytona in the method's class scope.
    source = (
        "from daytona import Daytona, AsyncDaytona\nfrom unittest.mock import MagicMock\n"
        "class Factory:\n    Daytona = AsyncDaytona\n"
        "    @staticmethod\n    def select() -> type[Daytona]:\n        return AsyncDaytona\n"
        "client = MagicMock(spec=Factory.select())\nclient.create()\n"
    )

    # When class-definition bindings take precedence over enclosing import metadata.
    result = migrate(source)

    # Then the outer synchronous spelling cannot certify this async-class factory.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
