# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""macOS creation forwards the requested disk persistence."""

import json

import httpx
import pytest
import respx

from daytona import AsyncDaytona, DaytonaConfig


@pytest.mark.anyio
@respx.mock
async def test_create_macos_sends_persistent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    route = respx.post("https://api.invalid/sandboxes/macos").mock(
        return_value=httpx.Response(200, json={"id": "sbx", "status": "running", "wait_cap_seconds": 3})
    )
    # When
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        await root.v2.create_macos("base-macos", persistent=True)
    # Then
    assert json.loads(route.calls[0].request.content)["persistent"] is True
