# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A sandbox wait ends on a read of the server when the events stream is silent.

The stream answers each subscribe with a snapshot and then sends no
transitions (its reconcile is minutes away). The hub gets the budget less a
final reserve; the reserve reads the state from the server, so a sandbox that
reaches the state mid-wait is seen within the budget, and one that never does
still times out. Timings are scaled down (the state is reached 0.4 s in), and
the hub's early probe is off, as it is in effect on a socket kept alive by
keepalive frames. The
TypeScript twin is ``tests/wait-final-read.test.ts``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Optional

import pytest
import respx
from _handles import lazy_sandbox
from test_core import _sandbox_detail_payload, bind_api
from test_events import _API_KEY, _API_URL, FakeConnector, FakeWS, ticket_mock

import daytona.v2._events as events_module
from daytona.v2._core import RequestSpec
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.errors import AuthenticationError, RequestTimeoutError, ServiceUnavailableError, create_error
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import DaytonaConfig, SandboxEventsConfig

pytestmark = pytest.mark.anyio

_ID = "sbx-region-a"
_REACHED_AFTER_S = 0.4


class _Sandbox:
    """A server-side sandbox that moves from ``before`` to ``after`` at a set time."""

    def __init__(self, before: str, after: str, reached_after: Optional[float]) -> None:
        self.before = before
        self.after = after
        self.reached_after = reached_after
        self.moved_at: Optional[float] = None
        self.reads = 0

    def move(self) -> None:
        self.moved_at = events_module._clock.monotonic()

    def status(self) -> str:
        if self.moved_at is None or self.reached_after is None:
            return self.before
        reached = events_module._clock.monotonic() - self.moved_at >= self.reached_after
        return self.after if reached else self.before


class _SnapshotOnlyWS(FakeWS):
    """Answers every subscribe with a snapshot of the current state, and nothing else."""

    def __init__(self, sandbox: _Sandbox) -> None:
        super().__init__()
        self._sandbox = sandbox
        self._version = 0

    async def send(self, data: str) -> None:
        await super().send(data)
        frame = json.loads(data)
        if frame.get("type") != "subscribe":
            return
        for entry in frame["ids"]:
            self._version += 1
            self.inject(
                {
                    "type": "snapshot",
                    "id": entry["id"],
                    "status": self._sandbox.status(),
                    "version": self._version,
                    "error": None,
                }
            )


class _Api:
    request_timeout_seconds = 30.0
    max_response_bytes = 0

    def __init__(self, sandbox: _Sandbox) -> None:
        self.sandbox = sandbox
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        if spec.method == "GET":
            self.sandbox.reads += 1
            return _sandbox_detail_payload(_ID, self.sandbox.status(), region="region-a")
        if spec.method == "POST" and spec.path == "/sandboxes":
            self.sandbox.move()
            return {"id": _ID, "status": self.sandbox.status(), "wait_cap_seconds": 0}
        self.sandbox.move()
        return {}

    async def close(self) -> None:
        return None


@contextlib.asynccontextmanager
async def _client(
    monkeypatch: pytest.MonkeyPatch, sandbox: _Sandbox, *, early_probe: float = 0
) -> AsyncIterator[tuple[AsyncDaytona, _Api]]:
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
    monkeypatch.setenv("DAYTONA_V2_API_URL", _API_URL)
    connector = FakeConnector()
    monkeypatch.setattr(events_module, "_default_connector", connector)
    for _ in range(4):
        connector.prepare(_SnapshotOnlyWS(sandbox))
    # The production reconcile (60 s) never fires within these budgets.
    client = AsyncDaytona(DaytonaConfig(api_key=_API_KEY, events=SandboxEventsConfig(reconcile_interval=60)))
    api = _Api(sandbox)
    bind_api(client, api)
    hub = client._acquire_hub()
    assert hub is not None
    # On a live socket the hub's keepalive frames keep its early probe from
    # firing; with it off, these waits see what production saw.
    hub._early_probe_s = early_probe
    try:
        with respx.mock:
            ticket_mock("region-a")
            yield client, api
        # The waits ran on the socket, not on the hub's polling fallback.
        assert connector.connection_count() >= 1
    finally:
        await client.close()


def _handle(client: AsyncDaytona) -> AsyncSandbox:
    return lazy_sandbox(client, _ID)


Call = Callable[[AsyncDaytona], Awaitable[object]]

_LIFECYCLE: list[tuple[str, str, str, Call]] = [
    ("start", "stopped", "running", lambda c: _handle(c).start(timeout=1)),
    ("stop", "running", "stopped", lambda c: _handle(c).stop(timeout=1)),
    ("delete", "deleting", "deleted", lambda c: _handle(c).delete(timeout=1)),
]


async def _timed(call: Callable[[], Awaitable[object]]) -> tuple[object, float]:
    started = events_module._clock.monotonic()
    result = await asyncio.wait_for(call(), timeout=5)
    return result, events_module._clock.monotonic() - started


@pytest.mark.parametrize(("verb", "before", "after", "call"), _LIFECYCLE, ids=[v[0] for v in _LIFECYCLE])
async def test_a_lifecycle_wait_sees_a_state_the_stream_never_reports(
    monkeypatch: pytest.MonkeyPatch, verb: str, before: str, after: str, call: Call
) -> None:
    sandbox = _Sandbox(before, after, _REACHED_AFTER_S)
    async with _client(monkeypatch, sandbox) as (client, _):
        # On main this raises RequestTimeoutError at the deadline.
        await _timed(lambda: call(client))


@pytest.mark.parametrize(("verb", "before", "after", "call"), _LIFECYCLE, ids=[v[0] for v in _LIFECYCLE])
async def test_a_lifecycle_wait_whose_state_never_comes_still_times_out(
    monkeypatch: pytest.MonkeyPatch, verb: str, before: str, after: str, call: Call
) -> None:
    sandbox = _Sandbox(before, after, None)
    async with _client(monkeypatch, sandbox) as (client, _):
        started = events_module._clock.monotonic()
        with pytest.raises(RequestTimeoutError) as raised:
            await asyncio.wait_for(call(client), timeout=5)
        elapsed = events_module._clock.monotonic() - started
    assert raised.value.source == "SDK", verb
    assert elapsed >= 0.9, verb


async def test_create_returns_started_when_the_stream_never_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox("pending", "running", _REACHED_AFTER_S)
    async with _client(monkeypatch, sandbox) as (client, _):
        # On main the handle comes back `creating` when the budget runs out.
        created, _ = await _timed(lambda: client.create("img", wait_seconds=2))
    assert isinstance(created, AsyncSandbox)
    assert created.state == "started"


async def test_create_still_returns_the_handle_when_the_sandbox_never_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = _Sandbox("pending", "running", None)
    async with _client(monkeypatch, sandbox) as (client, _):
        created, elapsed = await _timed(lambda: client.create("img", wait_seconds=1))
    assert isinstance(created, AsyncSandbox)
    assert created.state == "creating"
    assert elapsed >= 0.9


async def test_the_stream_gets_the_budget_less_the_reserve(monkeypatch: pytest.MonkeyPatch) -> None:
    # No read before the hub's share ends: with the stream silent, the
    # reserve's long poll is the wait's only read.
    sandbox = _Sandbox("stopped", "running", _REACHED_AFTER_S)
    async with _client(monkeypatch, sandbox) as (client, api):
        handle = _handle(client)
        await handle.start(timeout=1)
        assert [spec.method for spec in api.specs] == ["GET", "POST", "GET"]
        final = api.specs[-1]
        assert final.params.get("wait") == "running"
        # The hub had all but the reserve (a quarter of the 1 s budget).
        assert 0 < int(final.params["timeout_ms"]) <= 260


async def test_a_failed_read_in_the_reserve_keeps_the_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _Sandbox("stopped", "running", None)
    async with _client(monkeypatch, sandbox) as (client, api):

        async def busy(spec: RequestSpec) -> object:
            api.specs.append(spec)
            raise create_error("busy", 503, "SERVICE_UNAVAILABLE", "DAYTONA_API")

        monkeypatch.setattr(api, "send", busy)
        with pytest.raises(RequestTimeoutError) as raised:
            await _handle(client).wait(until="started", timeout=1)
    assert raised.value.source == "SDK"
    assert isinstance(raised.value.__cause__, ServiceUnavailableError)
    assert [spec.method for spec in api.specs] == ["GET"]


async def test_a_refused_credential_in_the_reserve_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _Sandbox("stopped", "running", None)
    refused = create_error("unauthorized", 401, "UNAUTHORIZED", "DAYTONA_API")
    async with _client(monkeypatch, sandbox) as (client, api):

        async def unauthorized(spec: RequestSpec) -> object:
            api.specs.append(spec)
            raise refused

        monkeypatch.setattr(api, "send", unauthorized)
        with pytest.raises(AuthenticationError) as raised:
            await _handle(client).wait(until="started", timeout=1)
    assert raised.value is refused


async def test_the_hubs_probes_hold_no_long_poll_past_its_share(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the early probe on, a probe asks the server to hold its long poll
    # no further than the end of the hub's share (1.5 s of a 2 s budget), and
    # its read ends there too, so the reserve's read does not start beside a
    # probe still held.
    monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
    sandbox = _Sandbox("stopped", "running", None)
    async with _client(monkeypatch, sandbox, early_probe=0.3) as (client, api):
        started = events_module._clock.monotonic()
        asked: list[tuple[float, int, float]] = []
        original = api.send

        async def recording_send(spec: RequestSpec) -> object:
            begun = events_module._clock.monotonic() - started
            asked.append((begun, int(spec.params.get("timeout_ms", 0)), spec.timeout or 0.0))
            return await original(spec)

        monkeypatch.setattr(api, "send", recording_send)
        with pytest.raises(RequestTimeoutError):
            await _handle(client).wait(until="started", timeout=2)
    probes = [entry for entry in asked if entry[0] < 1.45]
    assert probes
    # Neither the server's hold nor the read itself (its slack included)
    # outlasts the hub's share.
    assert all(begun * 1000 + held <= 1_550 for begun, held, _ in probes)
    assert all(begun + read_timeout <= 1.55 for begun, _, read_timeout in probes)


async def test_a_server_408_in_the_reserve_keeps_the_wait_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _Sandbox("stopped", "running", None)
    server_408 = create_error("server timeout", 408, "REQUEST_TIMEOUT", "DAYTONA_API")
    async with _client(monkeypatch, sandbox) as (client, api):

        async def timing_out(spec: RequestSpec) -> object:
            api.specs.append(spec)
            raise server_408

        monkeypatch.setattr(api, "send", timing_out)
        with pytest.raises(RequestTimeoutError) as raised:
            await _handle(client).wait(until="started", timeout=1)
    assert raised.value.source == "SDK"
    assert raised.value.__cause__ is server_408
