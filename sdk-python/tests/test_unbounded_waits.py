# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A wait budget above the timer maximum (about 24.8 days) is no deadline, for
every wait of the client, lifecycle and platform services, in both SDKs.

Each call gets ``timeout=3_000_000`` (about 34.7 days) while the SDK's clock
moves ten days forward on every reading: a real deadline would pass within
the first few reads. The call keeps waiting and returns once the state settles,
never with ``RequestTimeoutError``. The TypeScript twin is
``tests/unbounded-waits.test.ts``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import ImageBuilder, RequestTimeoutError
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_BUDGET = 3_000_000
_STEP = 10 * 86_400.0


def _detail(status: str, sandbox_id: str = "sbx") -> JsonObject:
    return {
        "id": sandbox_id,
        "class": "linux",
        "name": sandbox_id,
        "status": status,
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        "toolbox_proxy_url": "http://127.0.0.1:9/toolbox",
    }


def _image(status: str) -> JsonObject:
    return {
        "id": "img",
        "class": "linux",
        "status": status,
        "region_id": "region-a",
        "region_ids": ["region-a"],
        "anonymous": True,
        "retention": "idle",
        "origin": "registry",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "format_version": 2,
        "format_outdated": False,
        "platform": False,
        "default": False,
        "deprecated": False,
    }


@pytest.fixture(autouse=True)
def racing_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK's clock moves ten days forward on every reading."""
    now = [0.0]

    def monotonic() -> float:
        now[0] += _STEP
        return now[0]

    monkeypatch.setattr("daytona.v2._clock.monotonic", monotonic)
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "0")


_CASES: list[tuple[str, list[PlannedResponse], Callable[[AsyncDaytonaV2, str, float], Awaitable[object]]]] = [
    (
        "sandbox.stop",
        [
            PlannedResponse(200, {}),
            PlannedResponse(200, _detail("stopping")),
            PlannedResponse(200, _detail("stopping")),
            PlannedResponse(200, _detail("stopped")),
        ],
        lambda d, _url, budget: lazy_sandbox(d, "sbx").stop(timeout=budget),
    ),
    (
        "client.stop by id",
        [
            PlannedResponse(200, {}),
            PlannedResponse(200, _detail("stopping")),
            PlannedResponse(200, _detail("stopping")),
            PlannedResponse(200, _detail("stopped")),
        ],
        lambda d, _url, budget: d.stop("sbx", timeout=budget),
    ),
    (
        "sandbox.wait",
        [
            PlannedResponse(200, _detail("starting")),
            PlannedResponse(200, _detail("starting")),
            PlannedResponse(200, _detail("running")),
        ],
        lambda d, _url, budget: lazy_sandbox(d, "sbx").wait(until="started", timeout=budget),
    ),
    (
        "images.pull",
        [
            PlannedResponse(200, {"id": "img", "status": "pending"}),
            PlannedResponse(200, _image("pending")),
            PlannedResponse(200, _image("pending")),
            PlannedResponse(200, _image("pending")),
            PlannedResponse(200, _image("ready")),
        ],
        lambda d, _url, budget: d.images.pull("alpine", timeout=budget),
    ),
    (
        "images.get wait ready",
        [
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("ready")),
        ],
        lambda d, _url, budget: d.images.get("img", wait="ready", timeout=budget),
    ),
    (
        "docker.wait_until_ready",
        [
            PlannedResponse(200, {"owner": "daemon", "state": "starting"}),
            PlannedResponse(200, {"owner": "daemon", "state": "starting"}),
            PlannedResponse(200, {"owner": "daemon", "state": "ready"}),
        ],
        lambda d, url, budget: lazy_sandbox(
            d, "sbx", toolbox_url=url, access_token="tok"
        ).docker.wait_until_ready(timeout=budget),
    ),
    (
        "create_from_build build_timeout",
        [
            PlannedResponse(200, {"id": "img", "status": "pending"}),
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("ready")),
            PlannedResponse(200, {"id": "sbx", "status": "running"}),
        ],
        lambda d, _url, budget: d.create_from_build(
            ImageBuilder.from_registry("alpine"), build_timeout=budget, wait_seconds=0
        ),
    ),
]


@pytest.mark.parametrize(("name", "responses", "call"), _CASES, ids=[case[0] for case in _CASES])
async def test_a_budget_above_the_timer_maximum_is_no_deadline(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    responses: list[PlannedResponse],
    call: Callable[[AsyncDaytonaV2, str, float], Awaitable[object]],
) -> None:
    del name
    with FakeApi(responses) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await call(root.v2, fake.url, _BUDGET)
    # Every planned answer was read: the call waited through them all.
    assert len(fake.requests) == len(responses)


@pytest.mark.parametrize(("name", "responses", "call"), _CASES, ids=[case[0] for case in _CASES])
async def test_a_real_budget_ends_under_the_same_clock(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    responses: list[PlannedResponse],
    call: Callable[[AsyncDaytonaV2, str, float], Awaitable[object]],
) -> None:
    # 2,000,000 s (about 23 days) is a real deadline: it passes before the
    # call reads every planned answer.
    del name
    with FakeApi(responses) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(RequestTimeoutError):
                await call(root.v2, fake.url, 2_000_000)
    assert len(fake.requests) < len(responses)


@pytest.mark.parametrize(("budget", "settles"), [(_BUDGET, True), (2_000_000, False)])
async def test_a_fork_wait_above_the_timer_maximum_is_no_deadline(
    monkeypatch: pytest.MonkeyPatch, budget: float, settles: bool
) -> None:
    # The source's state read, the fork POST, the fork's detail read, then its state wait.
    responses = [
        PlannedResponse(200, _detail("running", "src")),
        PlannedResponse(
            200, {"id": "f", "status": "pending", "forked_from": "src", "toolbox_proxy_url": "http://p"}
        ),
        PlannedResponse(200, _detail("starting", "f")),
        PlannedResponse(200, _detail("starting", "f")),
        PlannedResponse(200, _detail("running", "f")),
    ]
    with FakeApi(responses) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            source = lazy_sandbox(root.v2, "src")
            source._update(sandbox_class="linux", raw_sandbox_class="linux")
            fork = await source.fork(wait_seconds=budget)
    # A fork returns its handle however the wait ends: a real deadline leaves
    # it at the state last read.
    assert (fork.state == "started") is settles
    assert (len(fake.requests) == len(responses)) is settles


async def test_a_fork_with_wait_seconds_zero_is_still_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fork_response = {"id": "f", "status": "pending", "forked_from": "src", "toolbox_proxy_url": "http://p"}
    with FakeApi([PlannedResponse(200, fork_response)]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await lazy_sandbox(root.v2, "src").fork(wait_seconds=0)
    assert [(request.method, request.path) for request in fake.requests] == [("POST", "/sandboxes/src/fork")]
