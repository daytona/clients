# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A wait budget above the timer maximum (about 24.8 days) is no deadline, for
every wait of the client, lifecycle and platform services, in both SDKs.

Each call gets ``timeout=3_000_000`` (about 34.7 days), and the SDK's clock
jumps 36 days once the call's first wait read is answered (after the wait's
deadline is armed): a real deadline would then have passed. The call keeps
waiting and returns once the state settles, never with
``RequestTimeoutError``. Under the same clock a budget of 2,000,000 s (23 days,
a real deadline) ends with what the call documents. The TypeScript twin is
``tests/unbounded-waits.test.ts``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import DockerWaitTimeoutError, ImageBuilder, RequestTimeoutError, _clock
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_BUDGET = 3_000_000
_JUMP = 36 * 86_400.0


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
def _no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "0")


def _jumping_clock(monkeypatch: pytest.MonkeyPatch, fake: FakeApi, jump_after: int) -> None:
    """The SDK's clock jumps 36 days once ``jump_after`` requests were served."""
    real = _clock.monotonic

    def monotonic() -> float:
        return real() + (_JUMP if len(fake.requests) >= jump_after else 0.0)

    monkeypatch.setattr("daytona.v2._clock.monotonic", monotonic)


_Call = Callable[[AsyncDaytonaV2, str, float], Awaitable[object]]
# (name, answers, call, the answer after which the clock jumps (the first wait
# read), the exact error a budget that runs out raises)
_CASES: list[tuple[str, list[PlannedResponse], _Call, int, type[RequestTimeoutError]]] = [
    (
        "sandbox.stop",
        [
            PlannedResponse(200, {}),
            PlannedResponse(200, _detail("stopping")),
            PlannedResponse(200, _detail("stopping")),
            PlannedResponse(200, _detail("stopped")),
        ],
        lambda d, _url, budget: lazy_sandbox(d, "sbx").stop(timeout=budget),
        2,
        RequestTimeoutError,
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
        2,
        RequestTimeoutError,
    ),
    (
        "sandbox.wait",
        [
            PlannedResponse(200, _detail("booting")),
            PlannedResponse(200, _detail("booting")),
            PlannedResponse(200, _detail("running")),
        ],
        lambda d, _url, budget: lazy_sandbox(d, "sbx").wait(until="started", timeout=budget),
        1,
        RequestTimeoutError,
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
        1,
        RequestTimeoutError,
    ),
    (
        "images.get wait ready",
        [
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("building")),
            PlannedResponse(200, _image("ready")),
        ],
        lambda d, _url, budget: d.images.get("img", wait="ready", timeout=budget),
        1,
        RequestTimeoutError,
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
        1,
        DockerWaitTimeoutError,
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
        3,
        RequestTimeoutError,
    ),
]


_PARAMS = ("name", "responses", "call", "jump_after", "expected")


@pytest.mark.parametrize(_PARAMS, _CASES, ids=[case[0] for case in _CASES])
async def test_a_budget_above_the_timer_maximum_is_no_deadline(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    responses: list[PlannedResponse],
    call: _Call,
    jump_after: int,
    expected: type[RequestTimeoutError],
) -> None:
    del name, expected
    with FakeApi(responses) as fake:
        _jumping_clock(monkeypatch, fake, jump_after)
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await call(root.v2, fake.url, _BUDGET)
    # Every planned answer was read: the call waited through them all.
    assert len(fake.requests) == len(responses)


@pytest.mark.parametrize(_PARAMS, _CASES, ids=[case[0] for case in _CASES])
async def test_a_real_budget_ends_under_the_same_clock(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    responses: list[PlannedResponse],
    call: _Call,
    jump_after: int,
    expected: type[RequestTimeoutError],
) -> None:
    # 2,000,000 s (about 23 days) is a real deadline: it passes before the
    # call reads every planned answer, and the call raises its own timeout.
    del name
    with FakeApi(responses) as fake:
        _jumping_clock(monkeypatch, fake, jump_after)
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(RequestTimeoutError) as raised:
                await call(root.v2, fake.url, 2_000_000)
    assert type(raised.value) is expected
    assert (raised.value.code, raised.value.source) == ("REQUEST_TIMEOUT", "SDK")
    # The answer after which the clock jumps was served, so the deadline
    # passed only because of the jump.
    assert jump_after <= len(fake.requests) < len(responses)


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
        PlannedResponse(200, _detail("booting", "f")),
        PlannedResponse(200, _detail("booting", "f")),
        PlannedResponse(200, _detail("running", "f")),
    ]
    with FakeApi(responses) as fake:
        # The clock jumps once the fork's detail read is answered.
        _jumping_clock(monkeypatch, fake, 3)
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            source = lazy_sandbox(root.v2, "src")
            source._update(sandbox_class="linux", raw_sandbox_class="linux")
            fork = await source.fork(wait_seconds=budget)
    # A fork returns its handle however the wait ends: a real deadline leaves
    # it at the state last read, after the request the clock jumps after.
    assert fork.id == "f"
    if settles:
        assert fork.state == "started"
        assert len(fake.requests) == len(responses)
    else:
        assert fork.state == "starting"
        assert 3 <= len(fake.requests) < len(responses)


async def test_a_fork_with_wait_seconds_zero_is_still_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fork_response = {"id": "f", "status": "pending", "forked_from": "src", "toolbox_proxy_url": "http://p"}
    with FakeApi([PlannedResponse(200, fork_response)]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await lazy_sandbox(root.v2, "src").fork(wait_seconds=0)
    assert [(request.method, request.path) for request in fake.requests] == [("POST", "/sandboxes/src/fork")]
