# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Every ``iter()`` reads a failed page again after a transient failure, at
most three reads, and the error that ends the walk carries the cursor of the
page that failed, so the walk resumes there with no item repeated or skipped.
The TypeScript twin is tests/iter-pages.test.ts; Go's is page_retry_test.go."""

from __future__ import annotations

import os
from typing import Optional

import httpx
import pytest
import respx

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import (
    BadRequestError,
    ClientTimeoutError,
    ConnectionError,
    InternalServerError,
    LimitExceededError,
    RateLimitError,
    ServiceUnavailableError,
    _pages,
)
from daytona.v2.errors import DaytonaError, create_error
from daytona.v2.types import ListResult

_BASE = "https://api.example.com"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def record(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(_pages, "_sleep", record)
    return recorded


def _sandbox(sandbox_id: str) -> dict[str, object]:
    return {
        "class": "linux",
        "cpu": 1,
        "id": sandbox_id,
        "image": {"id": "img_py", "name": "python:3.12"},
        "snapshot": None,
        "memory_mib": 1024,
        "name": sandbox_id,
        "port_map": [],
        "region_id": "r1",
        "disk_mib": 2048,
        "status": "running",
        "cores_max": 1,
        "toolbox_proxy_url": "http://toolbox",
    }


def _page(*ids: str, next_cursor: Optional[str] = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "sandboxes": [_sandbox(sandbox_id) for sandbox_id in ids],
            "next_cursor": next_cursor,
            "total": 0,
            "total_relation": "eq",
        },
    )


def _gateway_error(status: int = 502) -> httpx.Response:
    return httpx.Response(status, text="<html>bad gateway</html>", headers={"content-type": "text/html"})


def _client() -> AsyncDaytona:
    os.environ["DAYTONA_V2_API_URL"] = _BASE
    return AsyncDaytona(DaytonaConfig(api_key="key"))


def _cursor(request: httpx.Request) -> Optional[str]:
    return request.url.params.get("cursor")


@respx.mock
@pytest.mark.anyio
async def test_a_transient_page_failure_is_read_again_and_the_walk_completes(sleeps: list[float]) -> None:
    route = respx.get(f"{_BASE}/sandboxes/search").mock(
        side_effect=[_page("a", next_cursor="p2"), _gateway_error(502), _gateway_error(503), _page("b")]
    )
    async with _client() as root:
        ids = [sandbox.id async for sandbox in root.v2.iter()]

    assert ids == ["a", "b"]
    assert [_cursor(call.request) for call in route.calls] == [None, "p2", "p2", "p2"]
    assert len(sleeps) == 2
    assert 0.4 <= sleeps[0] <= 0.5 and 0.8 <= sleeps[1] <= 1.0


@respx.mock
@pytest.mark.anyio
async def test_the_error_that_ends_a_walk_carries_the_failed_page_cursor_and_resuming_skips_nothing(
    sleeps: list[float],
) -> None:
    route = respx.get(f"{_BASE}/sandboxes/search").mock(
        side_effect=[
            _page("a", "b", next_cursor="p2"),
            _gateway_error(),
            _gateway_error(),
            _gateway_error(),
            _page("c", next_cursor="p3"),
            _page("d"),
        ]
    )
    async with _client() as root:
        seen: list[str] = []
        with pytest.raises(InternalServerError) as raised:
            async for sandbox in root.v2.iter():
                seen.append(sandbox.id)
        assert raised.value.status_code == 502
        assert raised.value.page_cursor == "p2"
        assert len(route.calls) == 4

        seen.extend([sandbox.id async for sandbox in root.v2.iter(cursor=raised.value.page_cursor)])

    assert seen == ["a", "b", "c", "d"]
    assert len(sleeps) == 2


@respx.mock
@pytest.mark.anyio
async def test_a_first_page_failure_carries_no_cursor(sleeps: list[float]) -> None:
    respx.get(f"{_BASE}/sandboxes/search").mock(
        return_value=httpx.Response(400, json={"code": "BAD_REQUEST"})
    )
    async with _client() as root:
        with pytest.raises(BadRequestError) as raised:
            [sandbox async for sandbox in root.v2.iter()]

    assert raised.value.page_cursor is None
    assert sleeps == []


@respx.mock
@pytest.mark.anyio
async def test_a_dropped_connection_is_read_again(sleeps: list[float]) -> None:
    route = respx.get(f"{_BASE}/sandboxes/search").mock(
        side_effect=[httpx.ConnectError("reset"), _page("a", next_cursor="")]
    )
    async with _client() as root:
        assert [sandbox.id async for sandbox in root.v2.iter()] == ["a"]

    assert len(route.calls) == 2
    assert len(sleeps) == 1


@respx.mock
def test_the_sync_client_resumes_from_the_failed_page(sleeps: list[float]) -> None:
    respx.get(f"{_BASE}/sandboxes/search").mock(
        side_effect=[_page("a", next_cursor="p2"), *[_gateway_error(504)] * 3, _page("b")]
    )
    os.environ["DAYTONA_V2_API_URL"] = _BASE
    with Daytona(DaytonaConfig(api_key="key")) as root:
        seen: list[str] = []
        with pytest.raises(DaytonaError) as raised:
            for sandbox in root.v2.iter():
                seen.append(sandbox.id)
        seen.extend(sandbox.id for sandbox in root.v2.iter(cursor=raised.value.page_cursor))

    assert raised.value.page_cursor == "p2"
    assert seen == ["a", "b"]


@pytest.mark.parametrize(
    "error",
    [
        create_error("bad", 400, "BAD_REQUEST", "DAYTONA_API"),
        create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"),
        ClientTimeoutError(),
        LimitExceededError("limit", status_code=429, code="LIMIT_EXCEEDED", source="DAYTONA_API"),
        RateLimitError("slow down", status_code=429, code="RATE_LIMITED", retry_after_seconds=11),
    ],
)
@pytest.mark.anyio
async def test_an_error_that_retrying_cannot_fix_is_raised_at_once(
    error: DaytonaError, sleeps: list[float]
) -> None:
    calls: list[Optional[str]] = []

    async def fetch(cursor: Optional[str]) -> ListResult[str]:
        calls.append(cursor)
        raise error

    with pytest.raises(DaytonaError) as raised:
        [item async for item in _pages.iter_pages(fetch, "c1")]

    assert raised.value is error
    assert error.page_cursor == "c1"
    assert calls == ["c1"]
    assert sleeps == []


@pytest.mark.parametrize(
    ("error", "delay"),
    [
        (RateLimitError("slow down", status_code=429, retry_after_seconds=3), 3.0),
        (ServiceUnavailableError("busy", status_code=503, retry_after_ms=2500), 2.5),
        (ConnectionError("reset"), None),
    ],
)
@pytest.mark.anyio
async def test_a_retry_waits_at_least_the_server_hint(
    error: DaytonaError, delay: Optional[float], sleeps: list[float]
) -> None:
    results: list[object] = [error, ListResult(items=["x"], next_cursor=None)]

    async def fetch(cursor: Optional[str]) -> ListResult[str]:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, ListResult)
        return result

    assert [item async for item in _pages.iter_pages(fetch)] == ["x"]
    assert len(sleeps) == 1
    if delay is not None:
        assert sleeps[0] == delay
    assert error.page_cursor is None


@pytest.mark.anyio
async def test_a_cursor_cycle_still_ends_the_walk() -> None:
    pages = {None: ListResult(items=[1], next_cursor="a"), "a": ListResult(items=[2], next_cursor="a")}

    async def fetch(cursor: Optional[str]) -> ListResult[int]:
        return pages[cursor]

    assert [item async for item in _pages.iter_pages(fetch)] == [1, 2]


def test_errors_outside_a_walk_carry_no_page_cursor() -> None:
    assert create_error("x", 404, "NOT_FOUND", "DAYTONA_API").page_cursor is None
