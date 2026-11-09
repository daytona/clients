# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""An error body is read for a code only when it declares application/json,
on the client, lifecycle and platform-service calls too. The TypeScript twin
is tests/error-media-type.test.ts."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import ImageNotFoundError, NotFoundError
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2

pytestmark = pytest.mark.anyio

_ENVELOPE = '{"code":"IMAGE_NOT_FOUND","message":"no such image","source":"DAYTONA_API"}'

Call = Callable[[AsyncDaytonaV2], Awaitable[object]]
_CALLS: dict[str, Call] = {
    "images.get": lambda d: d.images.get("img"),
    "get": lambda d: d.get("sbx"),
}


@pytest.mark.parametrize("call", list(_CALLS))
@pytest.mark.parametrize("content_type", ["text/plain", "application/jsonl", "text/json"])
async def test_an_envelope_not_declared_as_json_is_only_the_message(call: str, content_type: str) -> None:
    with FakeApi([PlannedResponse(404, _ENVELOPE, {"Content-Type": content_type})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(NotFoundError) as raised:
                await _CALLS[call](root.v2)
    # The body is the message, with no code or source (as in TypeScript).
    assert not isinstance(raised.value, ImageNotFoundError)
    assert (raised.value.status_code, raised.value.message, raised.value.code, raised.value.source) == (
        404,
        _ENVELOPE,
        None,
        None,
    )


@pytest.mark.parametrize("call", list(_CALLS))
@pytest.mark.parametrize("content_type", ["application/json", "Application/JSON; charset=utf-8"])
async def test_an_envelope_declared_as_json_carries_its_code(call: str, content_type: str) -> None:
    with FakeApi([PlannedResponse(404, _ENVELOPE, {"Content-Type": content_type})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(ImageNotFoundError) as raised:
                await _CALLS[call](root.v2)
    assert (raised.value.code, raised.value.source, raised.value.message) == (
        "IMAGE_NOT_FOUND",
        "DAYTONA_API",
        "no such image",
    )
