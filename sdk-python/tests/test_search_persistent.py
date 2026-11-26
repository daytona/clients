# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, TypedDict

import pytest
import respx

from daytona import AsyncDaytona, Daytona, DaytonaConfig


class PersistenceOptions(TypedDict, total=False):
    persistent: Optional[bool]


CASES = [
    pytest.param({"persistent": True}, "true", id="true"),
    pytest.param({"persistent": False}, "false", id="false"),
    pytest.param({"persistent": None}, None, id="none"),
    pytest.param({}, None, id="unset"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("options", "expected"), CASES)
@respx.mock
async def test_async_search_filters_when_persistence_is_supplied(
    options: PersistenceOptions, expected: Optional[str]
) -> None:
    # Given
    route = respx.get("http://api.invalid/sandboxes/search").respond(
        200, json={"sandboxes": [], "total": 0, "total_relation": "eq"}
    )
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as client:
        # When
        await client.v2.search(**options)
    # Then
    assert route.call_count == 1
    assert route.calls[0].request.url.params.get_list("persistent") == (
        [] if expected is None else [expected]
    )


@pytest.mark.parametrize(("options", "expected"), CASES)
@respx.mock
def test_sync_search_filters_when_persistence_is_supplied(
    options: PersistenceOptions, expected: Optional[str]
) -> None:
    # Given
    route = respx.get("http://api.invalid/sandboxes/search").respond(
        200, json={"sandboxes": [], "total": 0, "total_relation": "eq"}
    )
    with Daytona(DaytonaConfig(api_key="key")) as client:
        # When
        client.v2.search(**options)
    # Then
    assert route.call_count == 1
    assert route.calls[0].request.url.params.get_list("persistent") == (
        [] if expected is None else [expected]
    )
