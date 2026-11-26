# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Both client surfaces retry only the transition-conflicting submission."""

from __future__ import annotations

from typing import Literal

import anyio
import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse
from test_client_lifecycle_contract import _detail
from test_linux_persistence import _IMAGE
from test_transition_retry import Clock
from typing_extensions import assert_never

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import ConflictError, _clock, _transition_retry
from daytona.v2._sync.sandbox import Sandbox
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import JsonObject

Operation = Literal["fork", "images", "snapshots"]
_SNAPSHOT: JsonObject = {
    "id": "artifact",
    "name": "saved",
    "status": "ready",
    "class": "linux",
    "kind": "memory",
    "region_id": "region-a",
    "region_ids": ["region-a"],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}


async def run_async(sandbox: AsyncSandbox, operation: Operation) -> str:
    match operation:
        case "fork":
            return (await sandbox.fork(wait_seconds=0)).id
        case "images":
            return (await sandbox.capture_image(name="saved")).id
        case "snapshots":
            return (await sandbox.create_snapshot("saved")).id
        case unreachable:
            assert_never(unreachable)


def run_sync(sandbox: Sandbox, operation: Operation) -> str:
    match operation:
        case "fork":
            return sandbox.fork(wait_seconds=0).id
        case "images":
            return sandbox.capture_image(name="saved").id
        case "snapshots":
            return sandbox.create_snapshot("saved").id
        case unreachable:
            assert_never(unreachable)


async def invoke(sync: bool, operation: Operation) -> str:
    if sync:

        def run() -> str:
            with Daytona(DaytonaConfig(api_key="key")) as root:
                return run_sync(lazy_sandbox(root.v2, "src"), operation)

        return await anyio.to_thread.run_sync(run)
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        return await run_async(lazy_sandbox(root.v2, "src"), operation)


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "generated-sync"])
@pytest.mark.parametrize("operation", ["fork", "images", "snapshots"])
async def test_transition_retry_reaches_each_operation(
    monkeypatch: pytest.MonkeyPatch, sync: bool, operation: Operation
) -> None:
    # Given one transition conflict before acceptance.
    clock = Clock()
    monkeypatch.setattr(_clock, "monotonic", clock.monotonic)
    monkeypatch.setattr(_transition_retry, "sleep", clock.sleep)
    responses = [
        PlannedResponse(
            409,
            {
                "code": "SANDBOX_TRANSITION_IN_PROGRESS",
                "source": "DAYTONA_API",
                "message": "busy",
                "retry_after_seconds": 3,
            },
        ),
        PlannedResponse(202, {"id": "artifact", "status": "pending", "forked_from": "src"}),
    ]
    if operation != "fork":
        responses.append(
            PlannedResponse(200, {**_IMAGE, "id": "artifact"} if operation == "images" else _SNAPSHOT)
        )
    with FakeApi(responses) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        # When submitting through the authored or generated surface.
        result = await invoke(sync, operation)
    # Then submission retries unchanged, while detail lookup runs only once.
    assert result == "artifact"
    assert clock.delays == [3]
    assert [request.path for request in api.requests[:2]] == [f"/sandboxes/src/{operation}"] * 2
    assert api.requests[0].body == api.requests[1].body
    assert len(api.requests) == (2 if operation == "fork" else 3)


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "generated-sync"])
@pytest.mark.parametrize("operation", ["fork", "images", "snapshots"])
async def test_other_conflicts_end_each_operation(
    monkeypatch: pytest.MonkeyPatch, sync: bool, operation: Operation
) -> None:
    # Given a conflict that the caller, not the retry helper, owns.
    with FakeApi(
        [
            PlannedResponse(
                409,
                {
                    "code": "NETWORK_POLICY_TRANSITION_IN_PROGRESS",
                    "source": "DAYTONA_API",
                    "message": "other transition",
                },
            )
        ]
    ) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        # When attempting any of the supported submissions.
        with pytest.raises(ConflictError) as raised:
            await invoke(sync, operation)
    # Then no retry or detail request follows.
    assert raised.value.code == "NETWORK_POLICY_TRANSITION_IN_PROGRESS"
    assert len(api.requests) == 1


@pytest.mark.anyio
async def test_fork_readiness_budget_starts_after_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a retry delay longer than the caller's readiness budget.
    clock = Clock()
    monkeypatch.setattr(_clock, "monotonic", clock.monotonic)
    monkeypatch.setattr(_transition_retry, "sleep", clock.sleep)
    with FakeApi(
        [
            PlannedResponse(200, _detail("src", "running")),
            PlannedResponse(
                409,
                {
                    "code": "SANDBOX_TRANSITION_IN_PROGRESS",
                    "message": "busy",
                    "source": "DAYTONA_API",
                    "retry_after_seconds": 5,
                },
            ),
            PlannedResponse(202, {"id": "artifact", "status": "pending", "forked_from": "src"}),
            PlannedResponse(200, _detail("src", "running")),
            PlannedResponse(200, _detail("artifact", "running")),
        ]
    ) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            # When the fork spends five seconds retrying before its one-second wait.
            fork = await lazy_sandbox(root.v2, "src").fork(wait_seconds=1)
    # Then the detail read still has the full wait budget.
    assert fork.state == "started"
    assert clock.now == 5
    assert len(api.requests) == 5
