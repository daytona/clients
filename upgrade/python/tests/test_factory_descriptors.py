# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Imported uncertain factories and type-only descriptor annotations stay manual."""

from __future__ import annotations

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("access", ["c.create()", 'getattr(c, "create")'])
@pytest.mark.parametrize("receiver", ["make", "make()"])
def test_reports_when_imported_decorated_factory_is_unproven(
    access: str, receiver: str
) -> None:
    # Given a consumer with no direct SDK import and a decorated client factory.
    mapping = load_mapping()
    sources = {
        "factory": (
            "from daytona import Daytona\ndef adapt(fn): return fn\n"
            "@adapt\ndef make() -> Daytona: return Daytona()\n"
        ),
        "app": f"from factory import make\nc = {receiver}\n{access}\n",
    }
    # When resolving the consumer through the project index.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then both the uncertain callable and its result require explicit review.
    assert access in result.source.splitlines()
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "# FIXME(daytona-upgrade):" in result.source


@pytest.mark.parametrize(
    "definition",
    [
        "@property\n    async def client(self) -> SDK: return unknown()",
        "@staticmethod\n    @property\n    def client(self) -> SDK: return unknown()",
        "@property\n    @classmethod\n    def client(self) -> SDK: return unknown()",
    ],
)
@pytest.mark.parametrize("alias", ["Daytona", "Missing"])
def test_reports_when_type_only_annotation_cannot_prove_descriptor_result(
    definition: str, alias: str
) -> None:
    # Given an async or stacked property with a type-checking-only return alias.
    module = "daytona" if alias == "Daytona" else "external"
    source = (
        "from daytona import Daytona\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        f"    from {module} import {alias} as SDK\n"
        f"class App:\n    {definition}\napp = App()\napp.client.create()\n"
    )
    # When inspecting a read that does not yield a proven synchronous client.
    result = migrate(source)
    # Then only the alias actually linked to Daytona justifies manual review.
    assert "app.client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == (
        ["fixme"] if alias == "Daytona" else []
    )


def test_routes_when_synchronous_property_has_type_only_client_annotation() -> None:
    # Given a normal property with a type-only client alias and an opaque body.
    source = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    from daytona import Daytona as SDK\nclass App:\n    @property\n"
        "    def client(self) -> SDK: return unknown()\n"
        "app = App()\napp.client.create()\n"
    )
    # When inspecting the ordinary synchronous property read.
    result = migrate(source)
    # Then the annotation still proves the normal descriptor result.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert not result.items
