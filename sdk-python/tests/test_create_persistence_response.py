# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Create handles prefer server-reported persistence, including snapshot restores."""

from __future__ import annotations

import anyio
import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2.types import JsonValue


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "generated-sync"])
@pytest.mark.parametrize("snapshot", [False, True], ids=["image", "snapshot"])
@pytest.mark.parametrize(
    ("reported", "expected"),
    [(True, True), (False, False), (None, None), ("invalid", None)],
)
async def test_create_retains_reported_persistence(
    monkeypatch: pytest.MonkeyPatch, sync: bool, snapshot: bool, reported: JsonValue, expected: bool | None
) -> None:
    # Given an accepted create whose persistence may be inherited from a snapshot.
    with FakeApi([PlannedResponse(201, {"id": "sbx", "status": "pending", "persistent": reported})]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        # When returning before any detail read.
        if sync:

            def run() -> bool | None:
                with Daytona(DaytonaConfig(api_key="key")) as root:
                    sandbox = (
                        root.v2.create_from_snapshot("snap", wait_seconds=0)
                        if snapshot
                        else root.v2.create(wait_seconds=0)
                    )
                    return sandbox.persistent

            observed = await anyio.to_thread.run_sync(run)
        else:
            async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
                sandbox = (
                    await root.v2.create_from_snapshot("snap", wait_seconds=0)
                    if snapshot
                    else await root.v2.create(wait_seconds=0)
                )
                observed = sandbox.persistent
    # Then the handle reflects a boolean response without an extra GET.
    assert observed is expected
    assert [request.method for request in api.requests] == ["POST"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reported", "requested", "expected"),
    [(False, True, False), (True, False, True), (None, True, True), ("invalid", False, False)],
)
async def test_create_response_precedes_request_fallback(
    monkeypatch: pytest.MonkeyPatch, reported: JsonValue, requested: bool, expected: bool
) -> None:
    # Given competing request and response persistence, or an unusable response value.
    with FakeApi([PlannedResponse(201, {"id": "sbx", "persistent": reported})]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            # When creating without a detail read.
            sandbox = await root.v2.create(persistent=requested, wait_seconds=0)
    # Then only a valid server observation overrides the requested fallback.
    assert sandbox.persistent is expected
