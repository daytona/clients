# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``fs.remove()``, ``grep()``, ``glob()``, ``replace()`` and ``chmod()`` take a
per-call ``timeout`` that replaces the client's request timeout for that call.
The TypeScript suite's tests/filesystem-call-timeouts.test.ts holds the same
expectations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

import httpx
import pytest
import respx

from daytona.v2 import BadRequestError, ClientTimeoutError
from daytona.v2._sync.filesystem import FileSystemService
from daytona.v2._transport import AsyncTransport
from daytona.v2.filesystem import AsyncFileSystemService

BASE = "http://toolbox.invalid"

Call = Callable[[AsyncFileSystemService, dict[str, object]], Awaitable[object]]

# method, route, call with keyword arguments, a valid response body
CALLS: list[tuple[str, str, str, Call, object]] = [
    ("remove", "DELETE", "/files", lambda fs, kw: fs.remove("/tree", recursive=True, **kw), None),  # type: ignore[arg-type]
    ("grep", "GET", "/files/find", lambda fs, kw: fs.grep("/tree", "needle", **kw), {"items": []}),  # type: ignore[arg-type]
    ("glob", "GET", "/files/search", lambda fs, kw: fs.glob("/tree", "*.py", **kw), {"files": []}),  # type: ignore[arg-type]
    (
        "replace",
        "POST",
        "/files/replace",
        lambda fs, kw: fs.replace(["/tree/a"], "old", "new", **kw),  # type: ignore[arg-type]
        {"items": []},
    ),
    (
        "chmod",
        "POST",
        "/files/permissions",
        lambda fs, kw: fs.chmod("/tree", mode="0755", **kw),  # type: ignore[arg-type]
        None,
    ),
]
IDS = [name for name, *_ in CALLS]


class _AnswersAfter:
    """A call the sandbox answers after ``delay`` seconds; counts the requests."""

    def __init__(self, delay: float, body: object) -> None:
        self.delay = delay
        self.body = body
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        return httpx.Response(200) if self.body is None else httpx.Response(200, json=self.body)


async def _call(call: Call, request_timeout: float, **kwargs: object) -> None:
    transport = AsyncTransport(BASE, "k", request_timeout=request_timeout)
    try:
        await call(AsyncFileSystemService(transport), kwargs)
    finally:
        await transport.close()


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(("name", "method", "route", "call", "body"), CALLS, ids=IDS)
async def test_without_a_timeout_the_request_timeout_applies(
    name: str, method: str, route: str, call: Call, body: object
) -> None:
    sandbox = _AnswersAfter(0.4, body)
    respx.route(method=method, url=f"{BASE}{route}").mock(side_effect=sandbox)
    with pytest.raises(ClientTimeoutError):
        await _call(call, 0.1)
    assert len(sandbox.requests) == 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("timeout", [2, 0, 3_000_000.0])
@pytest.mark.parametrize(("name", "method", "route", "call", "body"), CALLS, ids=IDS)
async def test_a_call_timeout_outlasts_a_shorter_request_timeout(
    name: str, method: str, route: str, call: Call, body: object, timeout: float
) -> None:
    # The call answers after 0.4 s, past the 0.1 s request timeout: a longer
    # per-call timeout, 0 and a value above the timer maximum all let it finish.
    sandbox = _AnswersAfter(0.4, body)
    respx.route(method=method, url=f"{BASE}{route}").mock(side_effect=sandbox)
    await _call(call, 0.1, timeout=timeout)
    assert len(sandbox.requests) == 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(("name", "method", "route", "call", "body"), CALLS, ids=IDS)
async def test_a_shorter_call_timeout_ends_the_call(
    name: str, method: str, route: str, call: Call, body: object
) -> None:
    sandbox = _AnswersAfter(0.4, body)
    respx.route(method=method, url=f"{BASE}{route}").mock(side_effect=sandbox)
    with pytest.raises(ClientTimeoutError) as excinfo:
        await _call(call, 30.0, timeout=0.1)
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    assert len(sandbox.requests) == 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "60"])
@pytest.mark.parametrize(("name", "method", "route", "call", "body"), CALLS, ids=IDS)
async def test_an_invalid_call_timeout_is_refused_before_any_request(
    name: str, method: str, route: str, call: Call, body: object, bad: object
) -> None:
    respx_route = respx.route(method=method, url=f"{BASE}{route}").mock(return_value=httpx.Response(200))
    with pytest.raises(BadRequestError) as excinfo:
        await _call(call, 30.0, timeout=bad)
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )
    assert respx_route.call_count == 0


@pytest.mark.parametrize("name", IDS)
def test_the_sync_surface_takes_the_same_timeout(name: str) -> None:
    for service in (FileSystemService, AsyncFileSystemService):
        parameter = inspect.signature(getattr(service, name)).parameters["timeout"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None
