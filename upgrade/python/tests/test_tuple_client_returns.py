# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal tuple returns certify annotated client slots only across every exit."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "body",
    [
        "    return client, False\n",
        "    if flag:\n        return AsyncDaytona(), True\n    return client, False\n",
    ],
)
def test_routes_returned_client_when_every_literal_tuple_proves_annotated_slot(
    body: str,
) -> None:
    # Given Harbor's conditional snapshot-client builder shape.
    source = (
        "from daytona import AsyncDaytona\ndef build(client: AsyncDaytona) -> tuple[AsyncDaytona, bool]:\n"
        + body
        + "client, close = build(AsyncDaytona())\nclient.snapshot.create()\n"
    )
    mapping = load_mapping()
    # When the declared tuple shape is backed by every returning path.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the extracted client routes without review.
    assert result.source.endswith("client.v0.snapshot.create()\n")
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "    return unknown()\n",
        "    if flag:\n        return client, True\n    return foreign, False\n",
        "    if flag:\n        return client, True\n",
        "    return client, False, extra\n",
    ],
)
def test_reviews_returned_client_when_any_tuple_exit_is_unproven(body: str) -> None:
    # Given an annotation without all-exit evidence for its client slot.
    source = (
        "from daytona import AsyncDaytona\ndef build(client: AsyncDaytona) -> tuple[AsyncDaytona, bool]:\n"
        + body
        + "client, close = build(AsyncDaytona())\nclient.snapshot.create()\n"
    )
    mapping = load_mapping()
    # When even one return or fallthrough cannot certify the slot.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then ordinary review-only tuple behavior survives.
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "v0"
        for node in ast.walk(ast.parse(result.source))
    )
    assert any(item["kind"] == "fixme" for item in result.items)


def test_routes_client_when_retry_wrapped_method_unpacks_builder_tuple() -> None:
    # Given Harbor's wrapped instance method and conditional snapshot builder.
    source = """from daytona import AsyncDaytona
from tenacity import retry
class Service:
    @retry(reraise=True)
    async def create_with_retry(self, daytona: AsyncDaytona):
        build_daytona, should_close = self.build(daytona)
        await build_daytona.snapshot.create()
    def build(self, daytona: AsyncDaytona) -> tuple[AsyncDaytona, bool]:
        if flag:
            return AsyncDaytona(), True
        return daytona, False
"""
    mapping = load_mapping()
    # When a binding-proven retry decorator preserves the method receiver.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the extracted builder client has the same routing as its source client.
    assert "await build_daytona.v0.snapshot.create()" in result.source
    assert result.items == ()


@pytest.mark.parametrize(
    "fallback", ["pass", "AsyncDaytona = foreign", "from foreign import AsyncDaytona"]
)
def test_routes_optional_constructor_only_when_sdk_import_is_sole_writer(
    fallback: str,
) -> None:
    # Given a guarded optional SDK import and a typed tuple-returning builder.
    source = f"""from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from daytona import AsyncDaytona
try:
    from daytona import AsyncDaytona
except ImportError:
    {fallback}
def build() -> tuple[AsyncDaytona, bool]:
    return AsyncDaytona(), True
client, close = build()
client.snapshot.create()
"""
    mapping = load_mapping()
    # When an absent SDK may leave the constructor unbound, but never foreign.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then only the sole-writer import can certify a successful constructor call.
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "v0"
        for node in ast.walk(ast.parse(result.source))
    ) is (fallback == "pass")
