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
