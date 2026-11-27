# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A retried fork observes the source again before choosing its wait state."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse
from test_client_lifecycle_contract import _detail
from test_transition_retry import Clock

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import SandboxEventsConfig, _clock, _transition_retry


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "generated-sync"])
@pytest.mark.parametrize(
    ("initial", "settled", "expected"),
    [("starting", "running", "started"), ("pausing", "paused", "paused"), ("stopping", "stopped", "stopped")],
)
async def test_fork_wait_follows_source_after_retry(
    monkeypatch: pytest.MonkeyPatch, sync: bool, initial: str, settled: str, expected: str
) -> None:
    # Given a source settling during submission backoff and a fork still pending.
    clock = Clock()
    monkeypatch.setattr(_clock, "monotonic", clock.monotonic)
    monkeypatch.setattr(_transition_retry, "sleep", clock.sleep)
    responses = [
        PlannedResponse(200, _detail("src", initial)),
        PlannedResponse(409, {"code": "SANDBOX_TRANSITION_IN_PROGRESS", "source": "DAYTONA_API"}),
        PlannedResponse(202, {"id": "fork", "status": "pending"}),
        PlannedResponse(200, _detail("src", settled)),
        PlannedResponse(200, _detail("fork", "pending")),
        PlannedResponse(200, _detail("fork", settled)),
    ]
    with FakeApi(responses) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        # When the authored or generated client forks with a readiness wait.
        if sync:

            def run() -> tuple[str, str]:
                with Daytona(DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled=False))) as root:
                    fork = lazy_sandbox(root.v2, "src").fork(wait_seconds=10)
                    return fork.id, fork.state

            observed = await anyio.to_thread.run_sync(run)
        else:
            async with AsyncDaytona(
                DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled=False))
            ) as root:
                fork = await lazy_sandbox(root.v2, "src").fork(wait_seconds=10)
                observed = fork.id, fork.state
    # Then the accepted fork waits for the settled state, not the original transition.
    assert observed == ("fork", expected)
    assert [urlsplit(request.path).path for request in api.requests] == [
        "/sandboxes/src",
        "/sandboxes/src/fork",
        "/sandboxes/src/fork",
        "/sandboxes/src",
        "/sandboxes/fork",
        "/sandboxes/fork",
    ]
    assert parse_qs(urlsplit(api.requests[-1].path).query)["wait"] == [settled]
