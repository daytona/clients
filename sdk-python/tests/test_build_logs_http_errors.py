# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Build-log follower HTTP errors: decoded before the retry decision, kept
as the final error or its cause."""

from __future__ import annotations

from collections.abc import AsyncIterator

import anyio
import anyio.lowlevel
import httpx
import pytest

from daytona.v2 import (
    BuildLogInterruptedError,
    DaytonaError,
    LimitExceededError,
    NotSupportedError,
    RateLimitError,
    ServiceUnavailableError,
)
from daytona.v2._codes import API_ERROR_CODES, CODE_STATUS
from daytona.v2._transport import AsyncTransport
from daytona.v2.build_logs import _PERMANENT_CODES, follow_build_logs

pytestmark = pytest.mark.anyio

DISCOVERY = "/images/image/build-logs-url"
LOGS_URL = "https://logs.example/build-logs/images/image"
DONE = b'event: done\ndata: {"status":"ready","reason":"terminal","output_complete":true}\n\n'


def error_response(status: int, code: str, message: str, *, retry_after: str | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    body = {"code": code, "message": message, "source": "DAYTONA_API", "trace_id": f"trace-{code.lower()}"}
    return httpx.Response(status, json=body, headers=headers)


def done() -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=DONE)


def setup(discoveries: list[httpx.Response], streams: list[httpx.Response] | None = None):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == DISCOVERY:
            return discoveries.pop(0) if discoveries else httpx.Response(200, json={"url": LOGS_URL})
        return streams.pop(0) if streams else done()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncTransport("https://api.example", "test-key", client=client), requests


@pytest.fixture(autouse=True)
def recorded_delays(monkeypatch) -> list[float]:
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)
        await anyio.lowlevel.checkpoint()

    monkeypatch.setattr("daytona.v2.build_logs._sleep", record)
    monkeypatch.setattr("daytona.v2.build_logs.random.uniform", lambda _a, _b: 1)
    return delays


def paths(requests: list[httpx.Request]) -> list[str]:
    return [request.url.path for request in requests]


async def test_transient_discovery_503_is_retried_then_succeeds(recorded_delays):
    # Given discovery answering one SERVICE_UNAVAILABLE before its URL.
    api, requests = setup([error_response(503, "SERVICE_UNAVAILABLE", "build-log resolver busy")])
    # When following.
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    # Then the 503 was one retried attempt, not the caller's answer.
    assert result.status == "ready"
    assert paths(requests) == [DISCOVERY, DISCOVERY, "/build-logs/images/image"]
    assert recorded_delays == [0.25]


@pytest.mark.parametrize("code", ["TOO_MANY_REQUESTS", "RATE_LIMITED"])
async def test_discovery_429_honours_retry_after_then_succeeds(code, recorded_delays):
    api, requests = setup([error_response(429, code, "slow down", retry_after="3")])
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert result.status == "ready"
    assert paths(requests) == [DISCOVERY, DISCOVERY, "/build-logs/images/image"]
    assert recorded_delays == [3]


async def test_exhausted_discovery_retries_keep_the_servers_typed_error_as_cause():
    # Given discovery that keeps answering 503 with a server error envelope.
    api, requests = setup(
        [
            error_response(503, "SERVICE_UNAVAILABLE", "build-log discovery is not configured")
            for _ in range(6)
        ]
    )
    # When following until the reconnect budget ends.
    with pytest.raises(BuildLogInterruptedError, match="reconnect budget") as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    # Then the server's status, code, message and trace ID are the cause.
    cause = failure.value.__cause__
    assert isinstance(cause, ServiceUnavailableError)
    assert cause.status_code == 503
    assert cause.code == "SERVICE_UNAVAILABLE"
    assert cause.message == "build-log discovery is not configured"
    assert cause.trace_id == "trace-service_unavailable"
    assert paths(requests) == [DISCOVERY] * 6


async def test_exhausted_stream_retries_keep_the_servers_typed_error_as_cause():
    api, requests = setup([], [error_response(502, "BAD_GATEWAY", "upstream reset") for _ in range(6)])
    with pytest.raises(BuildLogInterruptedError, match="reconnect budget") as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    cause = failure.value.__cause__
    assert isinstance(cause, DaytonaError)
    assert (cause.status_code, cause.code, cause.message) == (502, "BAD_GATEWAY", "upstream reset")
    assert cause.trace_id == "trace-bad_gateway"
    assert paths(requests) == [DISCOVERY] + ["/build-logs/images/image"] * 6


@pytest.mark.parametrize(
    "status,code,error_type",
    [
        (429, "LIMIT_EXCEEDED", LimitExceededError),
        (503, "V0_BACKUP_REGISTRY_UNCONFIGURED", ServiceUnavailableError),
        (501, "WORKLOAD_PLANE_ELSEWHERE", NotSupportedError),
        (501, "NOT_IMPLEMENTED", NotSupportedError),
        (501, "FUTURE_CODE", NotSupportedError),
    ],
)
async def test_permanent_discovery_failure_is_raised_without_retry(status, code, error_type, recorded_delays):
    # Given a 429 / 5xx whose code names a state a retry cannot change.
    api, requests = setup([error_response(status, code, "not here")])
    # When following.
    with pytest.raises(error_type) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    # Then the typed error is the answer at once, as a 404 is.
    assert not isinstance(failure.value, BuildLogInterruptedError)
    assert (failure.value.status_code, failure.value.code, failure.value.message) == (
        status,
        code,
        "not here",
    )
    assert paths(requests) == [DISCOVERY]
    assert recorded_delays == []


async def test_permanent_stream_failure_interrupts_without_retry(recorded_delays):
    api, requests = setup([], [error_response(429, "LIMIT_EXCEEDED", "quota reached")])
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert (failure.value.status_code, failure.value.code) == (429, "LIMIT_EXCEEDED")
    assert isinstance(failure.value.__cause__, RateLimitError)
    assert paths(requests) == [DISCOVERY, "/build-logs/images/image"]
    assert recorded_delays == []


async def test_a_501_without_a_readable_body_is_not_retried(recorded_delays):
    # Not implemented is permanent whatever its body says.
    api, requests = setup([httpx.Response(501, text="<html>not implemented</html>")])
    with pytest.raises(NotSupportedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert failure.value.status_code == 501
    assert paths(requests) == [DISCOVERY]
    assert recorded_delays == []


async def test_unreadable_5xx_body_is_still_retried(recorded_delays):
    # A gateway's HTML 502 carries no code: retried like every other 5xx.
    api, requests = setup([httpx.Response(502, text="<html>bad gateway</html>")])
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert result.status == "ready"
    assert paths(requests) == [DISCOVERY, DISCOVERY, "/build-logs/images/image"]


def test_permanent_codes_are_catalog_codes_of_retryable_statuses():
    for code in _PERMANENT_CODES:
        assert code in API_ERROR_CODES, code
        status = CODE_STATUS[code]
        # A 501 is permanent anyway; the list names retryable statuses only.
        assert status == 429 or (status >= 500 and status != 501), (code, status)


async def test_a_5xx_with_an_oversized_error_body_is_still_retried(recorded_delays):
    # The oversized body keeps its status, so the 503 stays retryable.
    big = httpx.Response(503, json={"code": "SERVICE_UNAVAILABLE", "message": "x" * (200 * 1024)})
    api, requests = setup([big])
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert result.status == "ready"
    assert paths(requests) == [DISCOVERY, DISCOVERY, "/build-logs/images/image"]


class _NeverEnds(httpx.AsyncByteStream):
    """An error body that starts and never finishes."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"code":"SERVICE_UNAVAILABLE",'
        await anyio.sleep(3600)


async def test_an_error_body_that_never_ends_is_bounded_without_a_request_deadline(
    monkeypatch, recorded_delays
):
    # Given no request timeout, no caller timeout, and a 503 whose body never ends.
    monkeypatch.setattr("daytona.v2.build_logs._ERROR_BODY_SECONDS", 0.05)
    requests: list[httpx.Request] = []
    discoveries = [httpx.Response(503, headers={"content-type": "application/json"}, stream=_NeverEnds())]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == DISCOVERY:
            return discoveries.pop(0) if discoveries else httpx.Response(200, json={"url": LOGS_URL})
        return done()

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = AsyncTransport("https://api.example", "test-key", client=client, request_timeout=0)
    # When following.
    with anyio.fail_after(5):
        result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    # Then the status alone was the error after the body budget, and it was retried.
    assert result.status == "ready"
    assert paths(requests) == [DISCOVERY, DISCOVERY, "/build-logs/images/image"]
