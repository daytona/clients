# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Callable

import anyio
import httpx
import pytest

from daytona.v2 import (
    AsyncSandbox,
    AsyncSandboxVolumesService,
    PullVolumeResult,
    Sandbox,
    SandboxVolumesService,
)
from daytona.v2._bridge import SyncBridge
from daytona.v2._core import WAIT_READ_SLACK_SECONDS
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import (
    BadRequestError,
    ConflictError,
    DaytonaError,
    RateLimitError,
    ServiceUnavailableError,
    UnsupportedOperationError,
)
from daytona.v2.sandbox_volumes import parse_pull_volume, pull_volume_spec


def async_transport(handler: Callable[[httpx.Request], httpx.Response], base_url: str) -> AsyncTransport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)
    return AsyncTransport(base_url, client=client)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_pull_pending_replay_and_transport_parity(asynchronous: bool) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        pending = len(requests) == 1
        return httpx.Response(
            202 if pending else 200,
            json={
                "requestId": "request_1",
                "status": "pending" if pending else "pulled",
                "manifestId": None if pending else "manifest-1",
                "futureField": True,
            },
        )

    results: list[PullVolumeResult] = []
    if asynchronous:

        async def run() -> None:
            transport = async_transport(handler, "http://sandbox.invalid/sbx%2F%21%27")
            sandbox = AsyncSandbox._from_api({"id": "sbx", "status": "running"}, None, transport)
            try:
                for timeout in (0, 30, None):
                    results.append(
                        await sandbox.volumes.pull("volume_1", request_id="request_1", timeout=timeout)
                    )
            finally:
                await transport._client.aclose()

        anyio.run(run)
    else:
        transport = async_transport(handler, "http://sandbox.invalid/sbx%2F%21%27")
        bridge = SyncBridge(name="Sandbox")
        sandbox = Sandbox(AsyncSandbox._from_api({"id": "sbx", "status": "running"}, None, transport), bridge)
        try:
            assert isinstance(sandbox.volumes, SandboxVolumesService)
            for timeout in (0, 30, None):
                results.append(sandbox.volumes.pull("volume_1", request_id="request_1", timeout=timeout))
        finally:
            bridge.call(transport._client.aclose)
            bridge.close()
    assert results == [
        PullVolumeResult(request_id="request_1", status="pending", manifest_id=None, raw_status="pending"),
        PullVolumeResult(
            request_id="request_1", status="pulled", manifest_id="manifest-1", raw_status="pulled"
        ),
        PullVolumeResult(
            request_id="request_1", status="pulled", manifest_id="manifest-1", raw_status="pulled"
        ),
    ]
    assert len(requests) == 3  # No automatic polling, submission, or new identity.
    assert all(request.method == "POST" for request in requests)
    assert all(request.url.raw_path == b"/sbx%2F%21%27/volumes/volume_1/pull" for request in requests)
    assert [json.loads(request.content) for request in requests] == [
        {"requestId": "request_1", "waitMs": 0},
        {"requestId": "request_1", "waitMs": 30000},
        {"requestId": "request_1"},
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("timeout", [None, 30, 0, 1.2345])
def test_pull_http_budget(asynchronous: bool, timeout: float | None) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"requestId": "r", "status": "pending", "manifestId": None})

    options = {} if timeout is None else {"timeout": timeout}
    if asynchronous:

        async def run() -> PullVolumeResult:
            transport = async_transport(handler, "http://sandbox.invalid")
            try:
                return await AsyncSandboxVolumesService(transport).pull("v", request_id="r", **options)
            finally:
                await transport._client.aclose()

        result = anyio.run(run)
    else:
        transport = async_transport(handler, "http://sandbox.invalid")
        bridge = SyncBridge(name="SandboxVolumesService")
        try:
            result = SandboxVolumesService(AsyncSandboxVolumesService(transport), bridge).pull(
                "v", request_id="r", **options
            )
        finally:
            bridge.call(transport._client.aclose)
            bridge.close()

    assert result == PullVolumeResult(
        request_id="r", status="pending", manifest_id=None, raw_status="pending"
    )
    assert len(requests) == 1  # Pending does not cause an automatic retry.
    expected_body = {"requestId": "r", **({} if timeout is None else {"waitMs": int(timeout * 1000)})}
    assert json.loads(requests[0].content) == expected_body
    budget = (30 if timeout is None else timeout) + WAIT_READ_SLACK_SECONDS
    assert requests[0].extensions["timeout"] == {
        "connect": budget,
        "read": budget,
        "write": budget,
        "pool": budget,
    }


@pytest.mark.parametrize("identifier", ["", "a/b", "..", "a%2Fb", "a?b", "a#b", "a b", "é", "a\n", "a" * 129])
def test_invalid_ids(identifier: str) -> None:
    with pytest.raises(BadRequestError):
        pull_volume_spec(identifier, request_id="r")
    with pytest.raises(BadRequestError):
        pull_volume_spec("v", request_id=identifier)


@pytest.mark.parametrize("timeout", [-1, 30.001, float("nan"), float("inf"), True, 10**999])
def test_invalid_wait(timeout: float) -> None:
    with pytest.raises(BadRequestError):
        pull_volume_spec("v", request_id="r", timeout=timeout)


def test_fractional_seconds_and_identifier_boundaries() -> None:
    spec = pull_volume_spec("v" * 128, request_id="r" * 128, timeout=1.2345)
    assert spec.json_body == {"requestId": "r" * 128, "waitMs": 1234}


@pytest.mark.parametrize(("timeout", "wait_ms"), [(0, 0), (0.0, 0), (0.0001, 1), (0.0009, 1), (0.001, 1)])
def test_sub_millisecond_timeout_stays_a_retry(timeout: float, wait_ms: int) -> None:
    # waitMs 0 is a check that never retries; a positive timeout must not become one.
    assert pull_volume_spec("v", request_id="r", timeout=timeout).json_body == {
        "requestId": "r",
        "waitMs": wait_ms,
    }


@pytest.mark.parametrize("manifest_id", [None, "manifest-2"])
def test_unchanged_nullable_manifest(manifest_id: str | None) -> None:
    assert parse_pull_volume(
        {"requestId": "r", "status": "unchanged", "manifestId": manifest_id}
    ) == PullVolumeResult(request_id="r", status="unchanged", manifest_id=manifest_id, raw_status="unchanged")


def test_unlisted_pull_status_passes_through() -> None:
    assert parse_pull_volume(
        {"requestId": "r", "status": "superseded", "manifestId": None}
    ) == PullVolumeResult(request_id="r", status="unknown", manifest_id=None, raw_status="superseded")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    ("status", "code", "error_class"),
    [
        (429, "TOO_MANY_REQUESTS", RateLimitError),
        (503, "SERVICE_UNAVAILABLE", ServiceUnavailableError),
        (409, "CONFLICT", ConflictError),
        (400, "UNSUPPORTED_OPERATION", UnsupportedOperationError),
    ],
)
def test_errors_propagate_without_retry(
    asynchronous: bool,
    status: int,
    code: str,
    error_class: type[DaytonaError],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Retry-After": "1"},
            json={
                "code": code,
                "message": "not ready",
                "source": "DAYTONA_DAEMON",
                "requestId": "r",
                "retryAfterMs": 1000,
            },
        )

    with pytest.raises(error_class) as caught:
        if asynchronous:

            async def run() -> None:
                transport = async_transport(handler, "http://sandbox.invalid")
                try:
                    await AsyncSandboxVolumesService(transport).pull("v", request_id="r")
                finally:
                    await transport._client.aclose()

            anyio.run(run)
        else:
            transport = async_transport(handler, "http://sandbox.invalid")
            bridge = SyncBridge(name="SandboxVolumesService")
            try:
                SandboxVolumesService(AsyncSandboxVolumesService(transport), bridge).pull("v", request_id="r")
            finally:
                bridge.call(transport._client.aclose)
                bridge.close()
    assert caught.value.status_code == status
    assert caught.value.request_id == "r"
    assert caught.value.retry_after_ms == 1000
    assert len(requests) == 1


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr("daytona.v2._clock.monotonic", clock.monotonic)
    monkeypatch.setattr("daytona.v2.sandbox_volumes._sleep", clock.sleep)
    return clock


def _rounds(
    statuses: list[str], clock: _FakeClock, *, held: bool = True
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    """A sandbox that holds each round for its waitMs (``held``) and answers
    the next status; the last status repeats."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if held:
            clock.now += json.loads(request.content)["waitMs"] / 1000
        status = statuses[min(len(requests), len(statuses)) - 1]
        pending = status == "pending"
        return httpx.Response(
            202 if pending else 200,
            json={"requestId": "r", "status": status, "manifestId": None if pending else "m"},
        )

    return handler, requests


async def _pull(handler: Callable[[httpx.Request], httpx.Response], timeout: float) -> PullVolumeResult:
    transport = async_transport(handler, "http://sandbox.invalid")
    try:
        return await AsyncSandboxVolumesService(transport).pull("v", request_id="r", timeout=timeout)
    finally:
        await transport._client.aclose()


def _read_bounds(requests: list[httpx.Request]) -> list[float]:
    return [request.extensions["timeout"]["read"] for request in requests]


@pytest.mark.anyio
async def test_a_long_pull_waits_in_rounds_until_a_result(fake_clock: _FakeClock) -> None:
    # Given a pull that stays pending for two 30-second rounds.
    handler, requests = _rounds(["pending", "pending", "pulled"], fake_clock)
    # When the caller waits up to 120 seconds.
    result = await _pull(handler, 120)
    # Then each request holds at most 30 s with the same identity, bounded at hold + slack.
    assert result.status == "pulled"
    assert [json.loads(request.content) for request in requests] == [{"requestId": "r", "waitMs": 30000}] * 3
    assert _read_bounds(requests) == [30 + WAIT_READ_SLACK_SECONDS] * 3
    assert fake_clock.sleeps == []


@pytest.mark.anyio
async def test_a_long_pull_ends_pending_at_the_caller_deadline(fake_clock: _FakeClock) -> None:
    handler, requests = _rounds(["pending"], fake_clock)
    result = await _pull(handler, 70)
    assert result.status == "pending"
    # 30 + 30 + the 10 seconds left: the last round never outlives the budget.
    assert [json.loads(request.content)["waitMs"] for request in requests] == [30000, 30000, 10000]
    assert _read_bounds(requests) == [35.0, 35.0, 15.0]


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [3_000_000.0, 10**999])
async def test_a_pull_without_a_deadline_keeps_waiting_in_rounds(
    fake_clock: _FakeClock, timeout: float
) -> None:
    # Above the timer maximum is no deadline, an integer too large for a float included.
    handler, requests = _rounds(["pending"] * 4 + ["unchanged"], fake_clock)
    result = await _pull(handler, timeout)
    assert result.status == "unchanged"
    assert [json.loads(request.content)["waitMs"] for request in requests] == [30000] * 5


@pytest.mark.anyio
async def test_a_round_answered_early_is_paced(fake_clock: _FakeClock) -> None:
    # A sandbox that answers pending without holding never makes a busy loop.
    handler, requests = _rounds(["pending", "pending", "pulled"], fake_clock, held=False)
    result = await _pull(handler, 60)
    assert result.status == "pulled"
    assert len(requests) == 3
    assert fake_clock.sleeps == [1.0, 1.0]


@pytest.mark.anyio
async def test_early_rounds_never_return_before_the_deadline(fake_clock: _FakeClock) -> None:
    # A pause that would cross the deadline is cut there, then checked once more.
    handler, requests = _rounds(["pending"], fake_clock, held=False)
    result = await _pull(handler, 30.5)
    assert result.status == "pending"
    assert fake_clock.now == pytest.approx(1030.5)
    assert fake_clock.sleeps[-1] == pytest.approx(0.5)
    assert json.loads(requests[-1].content)["waitMs"] == 1


@pytest.mark.anyio
async def test_a_failed_round_is_not_retried(fake_clock: _FakeClock) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            fake_clock.now += 30
            return httpx.Response(202, json={"requestId": "r", "status": "pending", "manifestId": None})
        return httpx.Response(
            503, json={"code": "SERVICE_UNAVAILABLE", "message": "busy", "source": "DAYTONA_DAEMON"}
        )

    with pytest.raises(ServiceUnavailableError):
        await _pull(handler, 120)
    assert len(requests) == 2


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), True, "60"])
def test_invalid_long_wait_is_refused_before_any_request(timeout: object) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    with pytest.raises(BadRequestError):
        anyio.run(_pull, handler, timeout)
    assert requests == []
