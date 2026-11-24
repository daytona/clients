# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``fs.move(..., timeout=)`` replaces the client's request timeout for one
move, against a delayed response; a move is never retried. The TypeScript
suite's tests/filesystem-move-timeout.test.ts holds the same expectations."""

from __future__ import annotations

import asyncio
import inspect

import httpx
import pytest
import respx

from daytona.v2 import BadRequestError, ClientTimeoutError
from daytona.v2._sync.filesystem import FileSystemService
from daytona.v2._transport import AsyncTransport
from daytona.v2.filesystem import AsyncFileSystemService

BASE = "http://toolbox.invalid"


class _AnswersAfter:
    """A move that the sandbox answers after ``delay`` seconds; counts the
    requests that reached it (respx records a call only once it answered)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        return httpx.Response(200)


async def _move(request_timeout: float, **kwargs: object) -> None:
    transport = AsyncTransport(BASE, "k", request_timeout=request_timeout)
    try:
        await AsyncFileSystemService(transport).move("/big", "/mnt/big", **kwargs)  # type: ignore[arg-type]
    finally:
        await transport.close()


@pytest.mark.anyio
@respx.mock
async def test_a_move_without_a_timeout_keeps_the_request_timeout_and_is_not_retried() -> None:
    sandbox = _AnswersAfter(0.4)
    respx.post(f"{BASE}/files/move").mock(side_effect=sandbox)
    with pytest.raises(ClientTimeoutError):
        await _move(0.1)
    assert len(sandbox.requests) == 1
    assert dict(sandbox.requests[0].url.params) == {"source": "/big", "destination": "/mnt/big"}


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("timeout", [2, 2.5, 0, 3_000_000.0])
async def test_a_move_timeout_outlasts_a_shorter_request_timeout(timeout: float) -> None:
    # The move answers after 0.4 s, past the 0.1 s request timeout: a longer
    # per-call timeout, 0 and a value above the timer maximum all let it finish.
    sandbox = _AnswersAfter(0.4)
    respx.post(f"{BASE}/files/move").mock(side_effect=sandbox)
    await _move(0.1, timeout=timeout)
    assert len(sandbox.requests) == 1


@pytest.mark.anyio
@respx.mock
async def test_a_shorter_move_timeout_ends_the_call_once_without_a_retry() -> None:
    sandbox = _AnswersAfter(0.4)
    respx.post(f"{BASE}/files/move").mock(side_effect=sandbox)
    with pytest.raises(ClientTimeoutError) as excinfo:
        await _move(30.0, timeout=0.1)
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    assert len(sandbox.requests) == 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "60"])
async def test_an_invalid_move_timeout_is_refused_before_any_request(bad: object) -> None:
    route = respx.post(f"{BASE}/files/move").mock(return_value=httpx.Response(200))
    with pytest.raises(BadRequestError) as excinfo:
        await _move(30.0, timeout=bad)
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )
    assert route.call_count == 0


def test_the_sync_move_takes_the_same_timeout() -> None:
    parameter = inspect.signature(FileSystemService.move).parameters["timeout"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
