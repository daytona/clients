# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Every ``iter()`` reads a failed page again after a transient failure, at
most three reads, and the error that ends the walk carries the cursor of the
page that failed, so the walk resumes there with no item repeated or skipped.
The TypeScript twin is tests/iter-pages.test.ts; Go's is page_retry_test.go."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, Optional

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


@pytest.fixture(autouse=True)
def _api_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_V2_API_URL", _BASE)


def _client() -> AsyncDaytona:
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

    copied = raised.value
    assert copied is not error and type(copied) is type(error)
    assert copied.args == error.args and str(copied) == str(error)
    assert (copied.status_code, copied.code, copied.source) == (error.status_code, error.code, error.source)
    assert copied.page_cursor == "c1"
    assert error.page_cursor is None
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


@pytest.mark.parametrize(
    ("header", "requests", "pauses"),
    [("3", 2, [3.0]), ("30", 1, [])],
    ids=["under 10 s is waited for", "over 10 s ends the walk without a retry"],
)
@respx.mock
@pytest.mark.anyio
async def test_a_retry_after_header_hint(
    header: str, requests: int, pauses: list[float], sleeps: list[float]
) -> None:
    route = respx.get(f"{_BASE}/sandboxes/search").mock(
        side_effect=[
            httpx.Response(
                429,
                json={"code": "RATE_LIMITED", "message": "slow down", "source": "DAYTONA_API"},
                headers={"retry-after": header},
            ),
            _page(),
        ]
    )
    async with _client() as root:
        if requests == 1:
            with pytest.raises(RateLimitError) as raised:
                [sandbox async for sandbox in root.v2.iter()]
            assert raised.value.page_cursor is None
        else:
            assert [sandbox async for sandbox in root.v2.iter()] == []

    assert len(route.calls) == requests
    assert sleeps == pauses


_ITERATORS = [
    ("sandboxes", "sandboxes", lambda root, _sandbox: root.v2.iter()),
    ("images", "images", lambda root, _sandbox: root.v2.images.iter()),
    ("snapshots", "snapshots", lambda root, _sandbox: root.v2.snapshots.iter()),
    ("volumes", "volumes", lambda root, _sandbox: root.v2.volumes.iter()),
    ("secrets", "secrets", lambda root, _sandbox: root.v2.secrets.iter()),
    ("reservations", "reservations", lambda root, _sandbox: root.v2.reservations.iter()),
    ("preview endpoints", "ports", lambda _root, sandbox: sandbox.ports.iter_endpoints()),
]


@pytest.mark.parametrize(("name", "field", "iterate"), _ITERATORS, ids=[name for name, _, _ in _ITERATORS])
@respx.mock
@pytest.mark.anyio
async def test_every_iterator_reads_a_transiently_failed_page_again(
    name: str, field: str, iterate: Callable[[AsyncDaytona, Any], AsyncIterator[object]], sleeps: list[float]
) -> None:
    respx.get(f"{_BASE}/sandboxes/sbx").mock(return_value=httpx.Response(200, json=_sandbox("sbx")))
    route = respx.get(host="api.example.com").mock(
        side_effect=[_gateway_error(503), httpx.Response(200, json={field: [], "next_cursor": None})]
    )
    async with _client() as root:
        sandbox = await root.v2.get("sbx") if field == "ports" else None
        assert [item async for item in iterate(root, sandbox)] == []

    assert len(route.calls) == 2
    assert len(sleeps) == 1


@pytest.mark.anyio
async def test_concurrent_walks_failing_with_one_shared_error_get_their_own_cursors() -> None:
    shared = create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API")
    both_started = asyncio.Event()
    started = 0

    async def fetch(cursor: Optional[str]) -> ListResult[str]:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        raise shared

    async def walk(cursor: str) -> DaytonaError:
        with pytest.raises(DaytonaError) as raised:
            [item async for item in _pages.iter_pages(fetch, cursor)]
        return raised.value

    first, second = await asyncio.gather(walk("cursor-A"), walk("cursor-B"))

    assert (first.page_cursor, second.page_cursor) == ("cursor-A", "cursor-B")
    assert shared.page_cursor is None


@pytest.mark.anyio
async def test_the_copy_keeps_the_cause_and_the_original_traceback() -> None:
    async def fetch(cursor: Optional[str]) -> ListResult[str]:
        try:
            raise OSError("reset")
        except OSError as exc:
            raise create_error("bad", 400, "BAD_REQUEST", "DAYTONA_API") from exc

    with pytest.raises(BadRequestError) as raised:
        [item async for item in _pages.iter_pages(fetch)]

    assert isinstance(raised.value.__cause__, OSError)
    frames = [entry.name for entry in raised.traceback]
    assert "fetch" in frames
