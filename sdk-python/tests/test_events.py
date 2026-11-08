# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Tests for sandbox state events.

Uses a fake WebSocket transport so no real server is needed.
respx intercepts ticket POST requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import pathlib
from typing import Callable, Dict, List, Optional, Tuple

import httpx
import pytest
import respx
from _handles import lazy_sandbox
from test_core import RecordingTransport, _sandbox_detail_payload, bind_api

from daytona.v2._events import (
    _HUB_REGISTRY,
    _KNOWN_IDS_CAP,
    _MAX_CREDENTIAL_REFUSALS,
    WaitHub,
    WaitOutcome,
    _evaluate,
    _TargetPredicate,
    _ws_url,
    attach_hub,
    detach_hub,
    region_of,
)
from daytona.v2.errors import (
    AuthenticationError,
    DaytonaError,
    NotFoundError,
    RequestTimeoutError,
    SandboxTerminalStateError,
    create_error,
)
from daytona.v2.types import SandboxEventsConfig


def _started_predicate() -> _TargetPredicate:
    """What every create/start wait registers: reach `started`, reject on a
    terminal state (the SDK's DEFAULT_SANDBOX_TERMINAL_STATES)."""
    return _TargetPredicate(
        states=frozenset({"started"}),
        terminal_states=frozenset({"destroying", "destroyed", "error"}),
    )


_API_URL = "https://api.test"


@pytest.fixture(autouse=True)
def _v2_api_url_is_the_test_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # The v2 client reads its API URL from the environment (no config field).
    monkeypatch.setenv("DAYTONA_V2_API_URL", _API_URL)


_API_KEY = "dtn_test_key"

_CONTRACT_PATH = pathlib.Path(__file__).parent.parent.parent / "contract" / "sandbox-events-protocol.json"


# ---------------------------------------------------------------------------
# Fake WebSocket
# ---------------------------------------------------------------------------


class FakeWS:
    """A fake WebSocket connection for testing."""

    def __init__(self) -> None:
        self._rx: asyncio.Queue[str | Exception] = asyncio.Queue()
        self._sent: List[tuple[float, str]] = []
        self._closed = False

    async def recv(self) -> str:
        item = await self._rx.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, data: str) -> None:
        self._sent.append((asyncio.get_running_loop().time(), data))

    async def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def inject(self, frame: dict) -> None:
        self._rx.put_nowait(json.dumps(frame))

    def inject_close(self, exc: Optional[Exception] = None) -> None:
        self._rx.put_nowait(exc or ConnectionError("closed"))

    def sent_frames(self) -> List[dict]:
        return [json.loads(data) for _, data in self._sent]

    def sent_frame_records(self) -> List[tuple[float, dict]]:
        return [(at, json.loads(data)) for at, data in self._sent]


class FakeConnector:
    """Injectable connector that produces FakeWS instances."""

    def __init__(self) -> None:
        self._connections: List[Tuple[str, List[str], FakeWS]] = []
        self._queue: asyncio.Queue[FakeWS] = asyncio.Queue()

    def prepare(self, ws: Optional[FakeWS] = None) -> FakeWS:
        w = ws or FakeWS()
        self._queue.put_nowait(w)
        return w

    async def __call__(self, uri: str, subprotocols: List[str]) -> FakeWS:
        ws = await self._queue.get()
        self._connections.append((uri, subprotocols, ws))
        return ws

    @property
    def last(self) -> FakeWS:
        return self._connections[-1][2]

    @property
    def last_uri(self) -> str:
        return self._connections[-1][0]

    @property
    def last_subprotocols(self) -> List[str]:
        return self._connections[-1][1]

    def connection_count(self) -> int:
        return len(self._connections)


def ticket_mock(region: str = "region-a", status: int = 200) -> respx.Route:
    url = f"{_API_URL}/events/{region}/ticket"
    if status == 200:
        return respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={"ticket": "t0k3n", "expires_in_ms": 30000, "subprotocol": "daytona.events.v1"},
            )
        )
    return respx.post(url).mock(return_value=httpx.Response(status))


def make_hub(
    connector: FakeConnector,
    *,
    enabled: bool = True,
    reconcile_interval: float = 0.0,
    idle_close: Optional[float] = None,
) -> WaitHub:
    cfg = SandboxEventsConfig(enabled=enabled, reconcile_interval=reconcile_interval, idle_close=idle_close)
    return WaitHub(
        _API_URL,
        _API_KEY,
        config=cfg,
        connector=connector,
    )


async def drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _wait_for(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# region_of tests
# ---------------------------------------------------------------------------


class TestWsUrl:
    """The socket URL keeps the API's base path — a deployment behind
    `https://host/platform/api` answers the upgrade there, not at the host
    root, and the ticket was minted through the full path. Same table as the
    TypeScript twin."""

    @pytest.mark.parametrize(
        ("api_url", "region", "expected"),
        [
            ("https://api.example.com", "region-a", "wss://api.example.com/events/region-a"),
            ("https://host/platform/api", "region-a", "wss://host/platform/api/events/region-a"),
            ("https://host/platform/api/", "region-a", "wss://host/platform/api/events/region-a"),
            ("http://127.0.0.1:8080", "_", "ws://127.0.0.1:8080/events/_"),
            ("http://127.0.0.1:8080/base", "_", "ws://127.0.0.1:8080/base/events/_"),
            ("https://host/platform/api?trace=1", "region-a", "wss://host/platform/api/events/region-a"),
            ("https://host/platform/api#frag", "region-a", "wss://host/platform/api/events/region-a"),
        ],
    )
    def test_ws_url(self, api_url: str, region: str, expected: str) -> None:
        assert _ws_url(api_url, region) == expected


class TestRegionOf:
    """Sandbox IDs select the same event region in both SDKs."""

    @pytest.mark.parametrize(
        ("sandbox_id", "expected", "why"),
        [
            ("7fJkQ2mN8pL3xR5tV9wA1b-region-a", "region-a", "tagged id"),
            ("550e8400-e29b-41d4-a716-446655440000", "_", "bare UUID carries no tag"),
            ("nodash", "_", "no delimiter"),
            ("abc-reg-extra", "reg-extra", "the slug may contain dashes"),
            ("", "_", "empty"),
            ("-region-a", "_", "a leading dash is not a tag"),
            ("abc-", "_", "a trailing dash is an empty tag"),
            ("-", "_", "a lone dash"),
        ],
    )
    def test_region_of(self, sandbox_id: str, expected: str, why: str) -> None:
        assert region_of(sandbox_id) == expected, why


# ---------------------------------------------------------------------------
# Predicate evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_in_progress_keeps_waiting(self) -> None:
        p = _started_predicate()
        assert _evaluate(p, "creating", "id") == "keep"
        assert _evaluate(p, "starting", "id") == "keep"

    def test_target_resolves_and_terminal_rejects(self) -> None:
        p = _started_predicate()
        assert _evaluate(p, "started", "id") == "resolve"
        assert _evaluate(p, "error", "id") == "reject"
        # A rest state that is neither the target nor terminal keeps waiting:
        # someone may yet start this sandbox.
        assert _evaluate(p, "stopped", "id") == "keep"

    def test_unknown_keeps_waiting(self) -> None:
        assert _evaluate(_started_predicate(), "unknown", "id") == "keep"

    def test_target_matches(self) -> None:
        p = _TargetPredicate(
            states=frozenset({"started"}),
            terminal_states=frozenset({"destroying", "destroyed", "error"}),
        )
        assert _evaluate(p, "started", "id") == "resolve"

    def test_target_terminal_rejects(self) -> None:
        p = _TargetPredicate(
            states=frozenset({"started"}),
            terminal_states=frozenset({"destroying", "destroyed", "error"}),
        )
        assert _evaluate(p, "error", "id") == "reject"
        assert _evaluate(p, "destroyed", "id") == "reject"

    def test_target_unknown_keeps(self) -> None:
        p = _TargetPredicate(
            states=frozenset({"started"}),
            terminal_states=frozenset({"error"}),
        )
        assert _evaluate(p, "unknown", "id") == "keep"

    def test_target_stopped_not_terminal(self) -> None:
        p = _TargetPredicate(
            states=frozenset({"started"}),
            terminal_states=frozenset({"destroying", "destroyed", "error"}),
        )
        assert _evaluate(p, "stopped", "id") == "keep"


# ---------------------------------------------------------------------------
# Hub tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestHubSnapshotThenLive:
    async def test_create_wait_snapshot_then_event(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()
            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            result = await task

        assert result.state == "started"

    async def test_snapshot_satisfies_immediately(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"started"}),
                        terminal_states=frozenset({"error", "destroyed"}),
                    ),
                    timeout=5,
                )
            )
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 3, "error": None}
            )
            result = await task

        assert result.state == "started"

    async def test_version_rule_lower_dropped(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 5, "error": None}
            )
            await drain()
            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "stopped", "version": 3, "error": None}
            )
            result = await task

        assert result.state == "started"

    async def test_version_rule_equal_applied(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"stopped"}),
                        terminal_states=frozenset({"error"}),
                    ),
                    timeout=5,
                )
            )
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 3, "error": None}
            )
            await drain()
            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "stopped", "version": 3, "error": None}
            )
            result = await task

        assert result.state == "stopped"

    async def test_unknown_keeps_waiting(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=2))
            await drain()
            ws.inject(
                {
                    "type": "snapshot",
                    "id": "abc-region-a",
                    "status": "unknownstate",
                    "version": 1,
                    "error": None,
                }
            )
            await drain()
            assert not task.done()
            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            result = await task

        assert result.state == "started"

    async def test_target_rejects_on_terminal(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"started"}),
                        terminal_states=frozenset({"destroying", "destroyed", "error"}),
                    ),
                    timeout=5,
                )
            )
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "failed", "version": 1, "error": None}
            )
            with pytest.raises(SandboxTerminalStateError) as exc_info:
                await task
        assert exc_info.value.state == "error"


@pytest.mark.anyio
class TestHubNotFound:
    async def test_not_found_was_known_true_rejects_target(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"started"}),
                        terminal_states=frozenset({"error"}),
                    ),
                    timeout=5,
                )
            )
            await drain()
            ws.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": True})
            with pytest.raises(NotFoundError):
                await task

    async def test_not_found_was_known_false_hands_the_wait_to_polling_fallback(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        polls = 0

        async def poll() -> WaitOutcome:
            nonlocal polls
            polls += 1
            return WaitOutcome("started", "running")

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"started"}),
                        terminal_states=frozenset({"error"}),
                    ),
                    timeout=2,
                    sandbox_poll_fn=poll,
                )
            )
            await drain()
            ws.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": False})
            result = await asyncio.wait_for(task, timeout=1)

        assert result.state == "started"
        assert polls == 1
        await asyncio.sleep(0.06)
        assert {"type": "unsubscribe", "ids": ["abc-region-a"]} in ws.sent_frames()

    async def test_real_frame_makes_client_knowledge_authoritative_for_not_found(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()
            ws.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": False})
            with pytest.raises(NotFoundError):
                await task


@pytest.mark.anyio
class TestHubTimeout:
    async def test_timeout_raises_request_timeout_error(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=0.1))
            await drain()
            with pytest.raises(RequestTimeoutError):
                await task

    async def test_a_wait_on_a_closed_hub_polls_instead_of_hanging(self) -> None:
        # A handle made before close() must still work: nothing runs a socket
        # loop on a closed hub, so a registered waiter would never be decided.
        fc = FakeConnector()
        hub = make_hub(fc)
        await hub._close_hub()
        polls = 0

        async def poll() -> WaitOutcome:
            nonlocal polls
            polls += 1
            return WaitOutcome("started", "running")

        outcome = await asyncio.wait_for(
            hub.wait("abc-region-a", _started_predicate(), timeout=0, sandbox_poll_fn=poll),
            timeout=2,
        )
        assert outcome.state == "started"
        assert polls == 1

    async def test_first_probe_uses_half_the_remaining_budget_when_that_is_smaller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        async def black_hole(_uri: str, _protos: List[str]) -> FakeWS:
            await asyncio.sleep(3600)
            return FakeWS()

        hub = WaitHub(
            _API_URL,
            _API_KEY,
            config=SandboxEventsConfig(enabled=True),
            connector=black_hole,
        )
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        hub._early_probe_s = 0.08
        polls = 0

        async def poll() -> WaitOutcome:
            nonlocal polls
            polls += 1
            return WaitOutcome("started", "running")

        with respx.mock:
            ticket_mock("region-a")
            result = await asyncio.wait_for(
                hub.wait("abc-region-a", _started_predicate(), timeout=0.06, sandbox_poll_fn=poll),
                timeout=1,
            )

        assert result.state == "started"
        assert polls == 1

    async def test_recurring_probe_resolves_when_upgrade_is_black_holed(self) -> None:
        async def black_hole(_uri: str, _protos: List[str]) -> FakeWS:
            await asyncio.sleep(3600)
            return FakeWS()

        hub = WaitHub(
            _API_URL,
            _API_KEY,
            config=SandboxEventsConfig(enabled=True),
            connector=black_hole,
        )
        hub._early_probe_s = 0.02
        polls = 0

        async def poll() -> WaitOutcome:
            nonlocal polls
            polls += 1
            return WaitOutcome("started", "running") if polls >= 5 else WaitOutcome("creating", "creating")

        with respx.mock:
            ticket_mock("region-a")
            result = await asyncio.wait_for(
                hub.wait("abc-region-a", _started_predicate(), timeout=5.0, sandbox_poll_fn=poll),
                timeout=2,
            )

        assert result.state == "started"
        assert polls >= 5

    async def test_snapshot_before_probe_fires_skips_the_early_probe(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._early_probe_s = 0.05
        polls = 0

        async def poll() -> WaitOutcome:
            nonlocal polls
            polls += 1
            return WaitOutcome("started", "running")

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait("abc-region-a", _started_predicate(), timeout=0.2, sandbox_poll_fn=poll)
            )
            await drain()
            await asyncio.sleep(0.02)
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await asyncio.sleep(0.025)
            await drain()
            assert not task.done()
            result = await asyncio.wait_for(task, timeout=1)

        assert result.state == "started"
        assert polls == 1

    async def test_healthy_socket_frames_keep_two_hundred_waiters_from_polling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        # 0.5 s probe vs a frame every 0.02 s. The probe window also covers the
        # setup before the first frame (200 waiters registering and the socket
        # connecting), which took over 0.1 s on loaded CI machines and let every
        # waiter poll before any frame arrived.
        hub._early_probe_s = 0.5
        hub._client_ping_after_s = 1.0
        poll_calls = 0

        async def poll() -> WaitOutcome:
            nonlocal poll_calls
            poll_calls += 1
            return WaitOutcome("started", "running")

        with respx.mock:
            ticket_mock("region-a")
            tasks = [
                asyncio.create_task(
                    hub.wait(
                        f"sandbox{index}-region-a",
                        _started_predicate(),
                        timeout=5.0,
                        sandbox_poll_fn=poll,
                    )
                )
                for index in range(200)
            ]
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)

            async def server_ping() -> None:
                for _ in range(40):
                    await asyncio.sleep(0.02)
                    ws.inject({"type": "ping"})
                    await drain()

            spammer = asyncio.create_task(server_ping())
            # Frames for 0.8 s, longer than one whole probe window.
            await spammer

            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        assert poll_calls == 0

    async def test_registering_a_waiter_on_a_held_snapshot_does_not_refresh_the_socket_quiet_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        hub._early_probe_s = 0.02
        poll_calls = 0

        async def poll() -> WaitOutcome:
            nonlocal poll_calls
            poll_calls += 1
            return WaitOutcome("started", "running")

        with respx.mock:
            ticket_mock("region-a")
            first = asyncio.create_task(
                hub.wait("abc-region-a", _started_predicate(), timeout=0.2, sandbox_poll_fn=poll)
            )
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()
            await asyncio.sleep(0.015)
            second = asyncio.create_task(
                hub.wait("abc-region-a", _started_predicate(), timeout=0.2, sandbox_poll_fn=poll)
            )
            await drain()
            await asyncio.sleep(0.01)
            await drain()
            assert poll_calls == 1
            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            assert (await asyncio.wait_for(first, timeout=1)).state == "started"
            assert (await asyncio.wait_for(second, timeout=1)).state == "started"

    async def test_frame_while_probe_poll_is_in_flight_makes_the_socket_decide(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._early_probe_s = 0.02
        polls = 0
        poll_started = asyncio.Event()
        poll_release: asyncio.Future[WaitOutcome] = asyncio.get_running_loop().create_future()

        async def poll() -> WaitOutcome:
            nonlocal polls
            polls += 1
            poll_started.set()
            return await poll_release

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(
                hub.wait("abc-region-a", _started_predicate(), timeout=0.2, sandbox_poll_fn=poll)
            )
            await asyncio.wait_for(poll_started.wait(), timeout=1)
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()
            poll_release.set_result(WaitOutcome("started", "running"))
            await drain()
            assert not task.done()
            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            result = await asyncio.wait_for(task, timeout=1)

        assert result.state == "started"
        assert polls == 1


@pytest.mark.anyio
class TestHubReconnect:
    async def test_reconnect_resubscribes_with_known(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws1 = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=10))
            await drain()

            ws1.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()
            assert "abc-region-a" in hub._known

            ws2 = fc.prepare()
            ws1.inject_close()
            await asyncio.sleep(0.5)
            await drain()

            assert fc.connection_count() == 2
            sub_frames = [f for f in ws2.sent_frames() if f.get("type") == "subscribe"]
            assert len(sub_frames) >= 1
            ids = {entry["id"]: entry["known"] for entry in sub_frames[0]["ids"]}
            assert ids.get("abc-region-a") is True

            ws2.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            result = await task

        assert result.state == "started"

    async def test_reconnect_requires_a_fresh_snapshot_before_reusing_cached_state(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws1 = fc.prepare()
            keep_waiting = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"paused"}),
                        terminal_states=frozenset({"destroying", "destroyed", "error"}),
                    ),
                    timeout=5,
                )
            )
            await drain()
            ws1.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await drain()

            ws2 = fc.prepare()
            ws1.inject_close()
            await asyncio.sleep(0.5)
            await drain()

            fresh_wait = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            assert not fresh_wait.done()

            ws2.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await drain()
            assert not fresh_wait.done()

            ws2.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await fresh_wait

        keep_waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keep_waiting
        assert result.state == "started"

    async def test_reconnect_accepts_a_newer_event_before_the_fresh_snapshot(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws1 = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws1.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()

            ws2 = fc.prepare()
            ws1.inject_close()
            await asyncio.sleep(0.5)
            await drain()

            ws2.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            result = await task

        assert result.state == "started"

    async def test_a_session_frame_resets_the_reconnect_backoff_to_the_first_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        mint_calls = 0

        def mint_response(request: httpx.Request) -> httpx.Response:
            nonlocal mint_calls
            mint_calls += 1
            return httpx.Response(
                200,
                json={"ticket": "t0k3n", "expires_in_ms": 30000, "subprotocol": "daytona.events.v1"},
            )

        monkeypatch.setattr(events_module, "_backoff", lambda attempt: 0.25 * (2**attempt))

        with respx.mock:
            respx.post(f"{_API_URL}/events/region-a/ticket").mock(side_effect=mint_response)
            ws1 = fc.prepare()
            ws2 = fc.prepare()
            ws3 = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            assert mint_calls == 1

            ws1.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 1, "error": None}
            )
            ws1.inject_close(ConnectionError("closed 1001"))
            await asyncio.sleep(0.2)
            await drain()
            assert mint_calls == 1
            await asyncio.wait_for(_wait_for(lambda: mint_calls == 2), timeout=1.0)

            ws2.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "pending", "version": 2, "error": None}
            )
            ws2.inject_close(ConnectionError("closed 1001"))
            await asyncio.sleep(0.2)
            await drain()
            assert mint_calls == 2
            await asyncio.wait_for(_wait_for(lambda: mint_calls == 3), timeout=1.0)

            ws3.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 3, "error": None}
            )
            assert (await asyncio.wait_for(task, timeout=5)).state == "started"

        await hub._close_hub()


@pytest.mark.anyio
class TestHubReconcile:
    async def test_reconcile_resubscribes(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc, reconcile_interval=0.1)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            len(ws.sent_frames())
            await asyncio.sleep(0.3)

            subs = [f for f in ws.sent_frames() if f.get("type") == "subscribe"]
            assert len(subs) > 1

            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            await task

    async def test_fallback_wait_uses_module_sleep_not_patched_asyncio_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        calls = 0
        pauses: List[float] = []

        async def fake_send(_request: object) -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"id": "abc-region-a", "status": "pending" if calls == 1 else "running"}

        async def fake_sleep(delay: float) -> None:
            pauses.append(delay)

        async def patched_asyncio_sleep(_delay: float) -> None:
            raise AssertionError("fallback pacing used asyncio.sleep")

        monkeypatch.setattr(hub._api, "send", fake_send)
        monkeypatch.setattr(events_module, "_sleep", fake_sleep)
        monkeypatch.setattr(asyncio, "sleep", patched_asyncio_sleep)

        result = await hub._fallback_wait("abc-region-a", _started_predicate(), timeout=1)
        assert result.state == "started"
        assert calls == 2
        assert pauses


@pytest.mark.anyio
class TestHubSharing:
    async def test_shared_hub_same_identity(self) -> None:
        fc = FakeConnector()
        cfg = SandboxEventsConfig(enabled=True, reconcile_interval=0.0)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()

            hub1 = attach_hub(
                _API_URL, _API_KEY, None, shared=True, source="sdk-python-async", config=cfg, connector=fc
            )
            hub2 = attach_hub(
                _API_URL, _API_KEY, None, shared=True, source="sdk-python-async", config=cfg, connector=fc
            )

            assert hub1 is hub2

            task1 = asyncio.create_task(hub1.wait("abc-region-a", _started_predicate(), timeout=5))
            task2 = asyncio.create_task(hub2.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            r1, r2 = await asyncio.gather(task1, task2)

            assert r1.state == r2.state == "started"

            await detach_hub(hub1, _API_URL, _API_KEY, None, shared=True)
            await detach_hub(hub2, _API_URL, _API_KEY, None, shared=True)

    async def test_close_one_does_not_break_other(self) -> None:
        fc = FakeConnector()
        cfg = SandboxEventsConfig(enabled=True, reconcile_interval=0.0)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()

            hub1 = attach_hub(
                _API_URL, _API_KEY, None, shared=True, source="sdk-python-async", config=cfg, connector=fc
            )
            hub2 = attach_hub(
                _API_URL, _API_KEY, None, shared=True, source="sdk-python-async", config=cfg, connector=fc
            )

            task = asyncio.create_task(hub2.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            await detach_hub(hub1, _API_URL, _API_KEY, None, shared=True)
            assert not hub1._closed

            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await task
            assert result.state == "started"

            await detach_hub(hub2, _API_URL, _API_KEY, None, shared=True)

    async def test_shared_hub_different_event_config_uses_a_distinct_hub(self) -> None:
        first = SandboxEventsConfig(enabled=True, reconcile_interval=0.0)
        second = SandboxEventsConfig(enabled=True, reconcile_interval=5.0)

        hub1 = attach_hub(
            _API_URL,
            _API_KEY,
            None,
            shared=True,
            source="sdk-python-async",
            config=first,
        )
        hub2 = attach_hub(
            _API_URL,
            _API_KEY,
            None,
            shared=True,
            source="sdk-python-async",
            config=second,
        )

        assert hub1 is not hub2

        await detach_hub(hub1, _API_URL, _API_KEY, None, shared=True)
        await detach_hub(hub2, _API_URL, _API_KEY, None, shared=True)

    async def test_hub_identity_lowercases_scheme_and_host_only(self) -> None:
        config = SandboxEventsConfig(enabled=True, reconcile_interval=0.0)

        hub1 = attach_hub(
            "HTTPS://API.EXAMPLE.com/Case/Path/",
            _API_KEY,
            None,
            shared=True,
            source="sdk-python-async",
            config=config,
        )
        hub2 = attach_hub(
            "https://api.example.com/Case/Path",
            _API_KEY,
            None,
            shared=True,
            source="sdk-python-async",
            config=config,
        )
        hub3 = attach_hub(
            "https://api.example.com/case/path",
            _API_KEY,
            None,
            shared=True,
            source="sdk-python-async",
            config=config,
        )

        assert hub1 is hub2
        assert hub3 is not hub1

        await detach_hub(hub1, "https://api.example.com/Case/Path", _API_KEY, None, shared=True)
        await detach_hub(hub2, "https://api.example.com/Case/Path", _API_KEY, None, shared=True)
        await detach_hub(hub3, "https://api.example.com/case/path", _API_KEY, None, shared=True)

    def test_shared_hub_prunes_closed_asyncio_run_loops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        api_url = "https://api.loop-prune.test"
        monkeypatch.setenv("DAYTONA_V2_API_URL", api_url)
        hub_holder: list = []

        async def create_and_hold() -> None:
            daytona = AsyncDaytona(DaytonaConfig(api_key=_API_KEY))
            hub = daytona._acquire_hub()
            if hub is not None:
                hub_holder.append(hub)

        asyncio.run(create_and_hold())
        matching = [key for key in _HUB_REGISTRY if key[1][0] == api_url]
        assert len(matching) == 1, "hub in registry while a strong reference exists"

        hub_holder.clear()
        gc.collect()
        gc.collect()
        matching = [key for key in _HUB_REGISTRY if key[1][0] == api_url]
        assert len(matching) == 0, "hub pruned from registry once GC'd (WeakValueDictionary)"

    async def test_private_hub_isolated(self) -> None:
        fc = FakeConnector()
        cfg = SandboxEventsConfig(enabled=True, reconcile_interval=0.0)

        hub1 = attach_hub(
            _API_URL, _API_KEY, None, shared=False, source="sdk-python-async", config=cfg, connector=fc
        )
        hub2 = attach_hub(
            _API_URL, _API_KEY, None, shared=False, source="sdk-python-async", config=cfg, connector=fc
        )

        assert hub1 is not hub2

        await hub1._close_hub()
        await hub2._close_hub()


@pytest.mark.anyio
class TestEndpointRekeying:
    async def test_region_socket_rekeyed_to_endpoint(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            assert "region-a" in hub._sockets

            hub.learn("abc-region-a", "endpoint-1")
            assert "endpoint-1" in hub._sockets
            assert "region-a" not in hub._sockets

            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await task
        assert result.state == "started"

    async def test_two_regions_with_the_same_endpoint_share_socket(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        hub.learn("id1-region-a", "endpoint-x")
        hub.learn("id2-region-b", "endpoint-x")

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task1 = asyncio.create_task(hub.wait("id1-region-a", _started_predicate(), timeout=5))
            task2 = asyncio.create_task(hub.wait("id2-region-b", _started_predicate(), timeout=5))
            await drain()

            assert fc.connection_count() == 1

            ws.inject(
                {"type": "snapshot", "id": "id1-region-a", "status": "running", "version": 1, "error": None}
            )
            ws.inject(
                {"type": "snapshot", "id": "id2-region-b", "status": "running", "version": 1, "error": None}
            )
            r1, r2 = await asyncio.gather(task1, task2)

        assert r1.state == r2.state == "started"

    async def test_remapping_a_region_moves_waiters_off_the_old_socket(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        hub.learn("abc-region-a", "endpoint-a")

        with respx.mock:
            ticket_mock("region-a")
            ws_old = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            ws_new = fc.prepare()
            hub.learn("abc-region-a", "endpoint-b")
            await drain()

            assert fc.connection_count() == 2

            ws_old.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await drain()
            assert not task.done()

            ws_new.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await task

        assert result.state == "started"


@pytest.mark.anyio
class TestMapEvictionAndRetry:
    async def test_not_found_on_map_routed_evicts_and_retries(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        hub.learn("abc-region-a", "endpoint-1")

        with respx.mock:
            ticket_mock("region-a")
            ws_primary = fc.prepare()
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"started"}),
                        terminal_states=frozenset({"error"}),
                    ),
                    timeout=5,
                )
            )
            await drain()

            ws_edge = fc.prepare()
            ws_primary.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": True})
            await drain()

            assert "region-a" not in hub._learned
            assert not task.done()

            ws_edge.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await task

            # The stale endpoint keeps the subscription — against this socket's and
            # the project's budgets — until it is told otherwise.
            await asyncio.sleep(0.06)
            assert {"type": "unsubscribe", "ids": ["abc-region-a"]} in ws_primary.sent_frames()

        assert result.state == "started"

    async def test_late_map_routed_not_found_does_not_re_subscribe_after_waiter_left(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        hub.learn("abc-region-a", "endpoint-1")

        with respx.mock:
            ticket_mock("region-a")
            ws_primary = fc.prepare()
            timed_out = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=0.05))
            await drain()

            with pytest.raises(RequestTimeoutError):
                await timed_out

            ws_primary.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": True})
            await drain()

            assert "region-a" not in hub._sockets

    async def test_stale_endpoint_frame_cannot_resolve_a_waiter_after_map_eviction(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        hub.learn("abc-region-a", "endpoint-1")

        with respx.mock:
            ticket_mock("region-a")
            ws_primary = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            ws_edge = fc.prepare()
            ws_primary.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": True})
            await drain()

            ws_primary.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            await drain()
            assert not task.done()

            ws_edge.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await task

        assert result.state == "started"


@pytest.mark.anyio
class TestFallback:
    async def test_404_ticket_uses_long_poll(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a", status=404)
            respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(
                return_value=httpx.Response(200, json={"status": "running", "id": "abc-region-a"})
            )

            result = await hub.wait("abc-region-a", _started_predicate(), timeout=5)

        assert result.state == "started"

    async def test_multi_target_fallback_skips_wait_query_params(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        seen_queries: list[dict[str, str]] = []

        def sandbox_detail(request: httpx.Request) -> httpx.Response:
            seen_queries.append(dict(request.url.params.multi_items()))
            status = "booting" if len(seen_queries) == 1 else "stopped"
            return httpx.Response(200, json={"status": status, "id": "abc-region-a"})

        with respx.mock:
            ticket_mock("region-a", status=404)
            respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(side_effect=sandbox_detail)

            result = await hub.wait(
                "abc-region-a",
                _TargetPredicate(
                    states=frozenset({"started", "stopped"}),
                    terminal_states=frozenset({"destroying", "destroyed", "error"}),
                ),
                timeout=5,
            )

        assert result.state == "stopped"
        assert seen_queries == [{}, {}]

    async def test_three_failures_triggers_transient_fallback(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        call_count = 0

        async def failing_connector(uri: str, subprotocols: list) -> FakeWS:
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("network error")
            ws = FakeWS()
            asyncio.get_event_loop().call_soon(
                lambda: ws.inject(
                    {
                        "type": "snapshot",
                        "id": "abc-region-a",
                        "status": "running",
                        "version": 1,
                        "error": None,
                    }
                )
            )
            return ws

        hub._connector = failing_connector

        with respx.mock:
            ticket_mock("region-a")
            respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(
                return_value=httpx.Response(200, json={"status": "running", "id": "abc-region-a"})
            )

            result = await hub.wait("abc-region-a", _started_predicate(), timeout=30)

        assert result.state == "started"
        assert call_count == 3

    async def test_sockets_that_open_and_never_send_still_reach_the_fallback(self) -> None:
        # A server that answers 101 and then says nothing: each socket opens
        # and dies with no frame. Opening must not reset the failure count, or
        # the 3-strike fallback is never reached and the wait just times out.
        fc = FakeConnector()
        hub = make_hub(fc)
        opened = 0

        class SilentWS(FakeWS):
            # Open, delivers nothing, then dies the way `websockets` ends a
            # socket whose pings go unanswered (ping_timeout): a closed error
            # after a while, with no frame ever received.
            async def recv(self) -> str:
                await asyncio.sleep(0.2)
                raise ConnectionError("keepalive ping timeout")

        async def silent_connector(uri: str, subprotocols: list) -> FakeWS:
            nonlocal opened
            opened += 1
            return SilentWS()

        hub._connector = silent_connector

        with respx.mock:
            ticket_mock("region-a")
            respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(
                return_value=httpx.Response(200, json={"status": "running", "id": "abc-region-a"})
            )
            result = await asyncio.wait_for(
                hub.wait("abc-region-a", _started_predicate(), timeout=60), timeout=15
            )

        assert result.state == "started"
        assert opened >= 3

    async def test_ping_and_pong_frames_refresh_the_liveness_watchdog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        monkeypatch.setattr(events_module, "_NO_FRAME_TIMEOUT_S", 0.2)

        async def inject_liveness_frame(frame: dict[str, object]) -> None:
            ws.inject(frame)
            await asyncio.sleep(0.03)
            await drain()

        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=1.0))
            await drain()

            await asyncio.sleep(0.04)
            await inject_liveness_frame({"type": "ping"})

            await asyncio.sleep(0.04)
            await inject_liveness_frame({"type": "pong"})

            await asyncio.sleep(0.04)
            await inject_liveness_frame({"type": "ping"})

            await asyncio.sleep(0.04)
            await inject_liveness_frame(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await asyncio.wait_for(task, timeout=1.0)

        assert result.state == "started"
        assert fc.connection_count() == 1
        assert [frame for frame in ws.sent_frames() if frame.get("type") == "pong"] == [
            {"type": "pong"},
            {"type": "pong"},
        ]

    async def test_client_ping_sends_once_per_quiet_window_and_pong_rearms_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        monkeypatch.setattr(events_module, "_NO_FRAME_TIMEOUT_S", 1.0)
        hub._client_ping_after_s = 0.02

        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=1.0))
            await drain()
            await asyncio.wait_for(
                _wait_for(
                    lambda: (
                        [frame for frame in ws.sent_frames() if frame.get("type") == "ping"]
                        == [{"type": "ping"}]
                    )
                ),
                timeout=1.0,
            )
            await asyncio.sleep(0.03)
            assert [frame for frame in ws.sent_frames() if frame.get("type") == "ping"] == [{"type": "ping"}]

            ws.inject({"type": "pong"})
            await drain()
            await asyncio.sleep(0.01)
            assert [frame for frame in ws.sent_frames() if frame.get("type") == "ping"] == [{"type": "ping"}]
            # The pong re-armed the ping: after another quiet window, a second one.
            await asyncio.wait_for(
                _wait_for(lambda: len([f for f in ws.sent_frames() if f.get("type") == "ping"]) == 2),
                timeout=1.0,
            )

            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await asyncio.wait_for(task, timeout=1.0)

        assert result.state == "started"

    async def test_a_new_connection_resets_the_liveness_watchdog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        fc.prepare()
        second = fc.prepare()
        monkeypatch.setattr(events_module, "_NO_FRAME_TIMEOUT_S", 0.05)
        monkeypatch.setattr(events_module, "_backoff", lambda attempt: 0.0)

        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=1.0))
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() >= 2), timeout=1.0)
            await asyncio.sleep(0.03)
            second.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            result = await asyncio.wait_for(task, timeout=1.0)

        assert result.state == "started"
        assert fc.connection_count() == 2

    async def test_any_socket_frame_postpones_the_early_probe_by_the_remaining_quiet_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        monkeypatch.setattr(events_module, "_EARLY_PROBE_S", 0.02)
        hub._early_probe_s = 0.02
        poll_calls = 0

        async def poll() -> WaitOutcome:
            nonlocal poll_calls
            poll_calls += 1
            return WaitOutcome("started", "running")

        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _started_predicate(),
                    timeout=0.2,
                    sandbox_poll_fn=poll,
                )
            )
            await drain()

            await asyncio.sleep(0.015)
            ws.inject(
                {"type": "snapshot", "id": "other-region-a", "status": "pending", "version": 1, "error": None}
            )
            await drain()
            await asyncio.sleep(0.011)
            await drain()
            assert poll_calls == 0
            assert not task.done()

            result = await asyncio.wait_for(task, timeout=0.05)

        assert result.state == "started"
        assert poll_calls == 1

    async def test_auth_error_on_ticket_raises(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a", status=401)
            with pytest.raises(AuthenticationError):
                await hub.wait("abc-region-a", _started_predicate(), timeout=5)


@pytest.mark.anyio
class TestProtocolParity:
    """Parity test: verify the hub correctly parses every fixture frame."""

    def test_fixture_exists(self) -> None:
        if not _CONTRACT_PATH.parent.is_dir():
            pytest.skip("the contracts are not in this tree (an sdist)")
        assert _CONTRACT_PATH.exists(), f"fixture not found: {_CONTRACT_PATH}"

    async def test_server_frames_parsed(self) -> None:
        if not _CONTRACT_PATH.exists():
            pytest.skip("contract fixture not found")

        fixture = json.loads(_CONTRACT_PATH.read_text())
        server_frames = fixture["server_frames"]
        sdk_state_decoding = fixture["sdk_state_decoding"]

        for frame_def in server_frames:
            fc = FakeConnector()
            hub = make_hub(fc)
            frame = dict(frame_def["frame"])
            if (
                frame_def["name"]
                not in {"error_malformed_frame", "error_malformed_id", "error_limit_inbound", "ping", "pong"}
                and frame.get("id") is not None
            ):
                frame["id"] = "abc-region-a"
            with respx.mock:
                ticket_mock("region-a")
                ws = fc.prepare()
                task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
                await drain()
                match frame_def["name"]:
                    case "snapshot" | "event" | "event_failed":
                        assert isinstance(frame.get("status"), str), frame_def["name"]
                        wire = str(frame.get("status") or "")
                        expected_sdk = sdk_state_decoding.get(wire, sdk_state_decoding.get("*", "unknown"))
                        ws.inject(frame)
                        for _ in range(30):
                            await drain()
                        if expected_sdk == "started":
                            assert task.done(), frame_def["name"]
                            result = await task
                            assert result.state == "started"
                            assert result.raw_state == wire
                        elif expected_sdk == "error":
                            assert task.done(), frame_def["name"]
                            with pytest.raises(SandboxTerminalStateError):
                                await task
                        else:
                            assert not task.done(), frame_def["name"]
                            ws.inject(
                                {
                                    "type": "event",
                                    "id": "abc-region-a",
                                    "status": "running",
                                    "version": 99,
                                    "error": None,
                                }
                            )
                            result = await task
                            assert result.state == "started"
                    case "error_not_found":
                        ws.inject(frame)
                        with pytest.raises(NotFoundError):
                            await task
                    case (
                        "error_limit"
                        | "error_limit_inbound"
                        | "error_malformed_frame"
                        | "error_malformed_id"
                        | "ping"
                        | "pong"
                    ):
                        ws.inject(frame)
                        await drain()
                        assert not task.done(), frame_def["name"]
                        ws.inject(
                            {
                                "type": "event",
                                "id": "abc-region-a",
                                "status": "running",
                                "version": 99,
                                "error": None,
                            }
                        )
                        result = await task
                        assert result.state == "started"
                    case other:
                        raise AssertionError(f"unhandled fixture frame {other}")

    async def test_client_subscribe_frame_shape(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            sent = ws.sent_frames()
            sub = next((f for f in sent if f.get("type") == "subscribe"), None)
            assert sub is not None
            assert "ids" in sub
            assert isinstance(sub["ids"], list)
            for entry in sub["ids"]:
                assert "id" in entry
                assert "known" in entry

            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await task

    async def test_pong_sent_on_ping(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()

            ws.inject({"type": "ping"})
            await drain()

            sent = ws.sent_frames()
            pongs = [f for f in sent if f.get("type") == "pong"]
            assert len(pongs) >= 1

            ws.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            await task


@pytest.mark.anyio
class TestUnsubscribeOnNoWaiters:
    async def test_unsubscribe_sent_when_no_more_waiters(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await task

        await asyncio.sleep(0.06)
        await drain()
        unsubs = [f for f in ws.sent_frames() if f.get("type") == "unsubscribe"]
        assert len(unsubs) >= 1
        assert "abc-region-a" in unsubs[0]["ids"]
        assert hub._sockets["region-a"].versions == {}

    async def test_four_hundred_resolutions_in_one_tick_send_one_unsubscribe_frame(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()

        with respx.mock:
            ticket_mock("region-a")
            respx.route(method="GET", url__regex=r"https://api\.test/sandboxes/.*").mock(
                return_value=httpx.Response(200, json={"id": "fallback", "status": "running"})
            )
            bootstrap = asyncio.create_task(hub.wait("bootstrap-region-a", _started_predicate(), timeout=30))
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)
            await asyncio.sleep(0.06)
            ws.inject(
                {
                    "type": "snapshot",
                    "id": "bootstrap-region-a",
                    "status": "running",
                    "version": 1,
                    "error": None,
                }
            )
            assert (await asyncio.wait_for(bootstrap, timeout=5)).state == "started"
            await asyncio.sleep(0.06)

            baseline = len([frame for frame in ws.sent_frames() if frame.get("type") == "unsubscribe"])
            ids = [f"id{index}-region-a" for index in range(400)]
            waits = [
                asyncio.create_task(hub.wait(sandbox_id, _started_predicate(), timeout=30))
                for sandbox_id in ids
            ]
            await asyncio.wait_for(
                _wait_for(lambda: sum(len(waiters) for waiters in hub._waiters.values()) == 400),
                timeout=1.0,
            )
            await asyncio.sleep(0.06)
            # The batching window is time-based: widen it so all 400
            # resolutions land in one window even on a slow runner (at 50 ms a
            # loaded CI host could split them across two flushes).
            hub._subscribe_flush_s = 1.0
            for index, sandbox_id in enumerate(ids, start=1):
                ws.inject(
                    {
                        "type": "snapshot",
                        "id": sandbox_id,
                        "status": "running",
                        "version": index,
                        "error": None,
                    }
                )
            results = await asyncio.gather(*waits)
            assert len(results) == 400

            def unsubscribe_count() -> int:
                return len([frame for frame in ws.sent_frames() if frame.get("type") == "unsubscribe"])

            await asyncio.wait_for(_wait_for(lambda: unsubscribe_count() > baseline), timeout=5)

            unsubscribe_frames = [frame for frame in ws.sent_frames() if frame.get("type") == "unsubscribe"][
                baseline:
            ]
            assert len(unsubscribe_frames) == 1
            assert len(unsubscribe_frames[0]["ids"]) == len(ids)
            assert set(unsubscribe_frames[0]["ids"]) == set(ids)

        await hub._close_hub()

    async def test_steady_resolved_stream_stays_within_fifty_frames_per_second(self) -> None:
        hub = make_hub(FakeConnector())
        ws = FakeWS()
        sock = hub._ensure_socket("region-a", "region-a")
        sock.ws = ws
        baseline = len(ws.sent_frame_records())
        ids = [f"steady-{index}-region-a" for index in range(300)]

        async def simulate_wait(sandbox_id: str, start_delay_s: float) -> None:
            await asyncio.sleep(start_delay_s)
            sock.subscriptions.add(sandbox_id)
            hub._waiters[sandbox_id] = [object()]  # type: ignore[list-item]
            hub._enqueue_subscribe_ids(sock, [sandbox_id])
            await asyncio.sleep(0.08)
            hub._waiters.pop(sandbox_id, None)
            hub._release_subscription(sock, sandbox_id)

        await asyncio.gather(
            *(
                asyncio.create_task(simulate_wait(sandbox_id, index * 0.004))
                for index, sandbox_id in enumerate(ids)
            )
        )
        await asyncio.sleep(0.10)

        frame_records = [
            (at, frame)
            for at, frame in ws.sent_frame_records()[baseline:]
            if frame.get("type") in {"subscribe", "unsubscribe"}
        ]
        for at, _frame in frame_records:
            frames_in_window = [record for record in frame_records if at <= record[0] < at + 1.0]
            assert len(frames_in_window) <= 50

        unsubscribe_ids = [
            sandbox_id
            for _, frame in frame_records
            if frame.get("type") == "unsubscribe"
            for sandbox_id in frame["ids"]
        ]
        assert len(unsubscribe_ids) == len(ids)
        assert set(unsubscribe_ids) == set(ids)
        for sandbox_id in ids:
            assert unsubscribe_ids.count(sandbox_id) == 1

        await hub._close_hub()


class TestKnownIds:
    def test_known_ids_are_lru_bounded(self) -> None:
        hub = make_hub(FakeConnector())

        for index in range(_KNOWN_IDS_CAP + 5):
            hub._remember_known(f"id-{index}")

        assert len(hub._known) == _KNOWN_IDS_CAP
        assert "id-0" not in hub._known
        assert f"id-{_KNOWN_IDS_CAP + 4}" in hub._known

        hub._remember_known("id-5")

        assert next(iter(hub._known)) == "id-6"


@pytest.mark.anyio
class TestCancelWait:
    async def test_cancel_removes_waiter(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)

        with respx.mock:
            ticket_mock("region-a")
            fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=10))
            await drain()

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert not hub._waiters.get("abc-region-a")


@pytest.mark.anyio
class TestRefusedSubscription:
    """A refusal is an answer, not silence: the server said it is sending no
    snapshot for this id, so a waiter that keeps waiting waits forever."""

    async def test_limit_retries_the_subscribe(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            ws.inject(
                {
                    "type": "error",
                    "id": "abc-region-a",
                    "code": "LIMIT",
                    "message": "subscriptions per socket",
                }
            )
            # The hub pauses, then asks again rather than waiting for a
            # snapshot the server already said it would not send.
            for _ in range(60):
                await asyncio.sleep(0.05)
                if len([f for f in ws.sent_frames() if f.get("type") == "subscribe"]) >= 2:
                    break
            subs = [f for f in ws.sent_frames() if f.get("type") == "subscribe"]
            assert len(subs) >= 2, subs
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            assert (await asyncio.wait_for(task, timeout=5)).state == "started"
        await hub._close_hub()

    async def test_limit_retries_are_batched_per_socket(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            first = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            second = asyncio.create_task(hub.wait("def-region-a", _started_predicate(), timeout=30))
            await drain()
            initial_frames = len([f for f in ws.sent_frames() if f.get("type") == "subscribe"])
            ws.inject(
                {
                    "type": "error",
                    "id": "abc-region-a",
                    "code": "LIMIT",
                    "message": "subscriptions per socket",
                }
            )
            ws.inject(
                {
                    "type": "error",
                    "id": "def-region-a",
                    "code": "LIMIT",
                    "message": "subscriptions per socket",
                }
            )
            for _ in range(60):
                await asyncio.sleep(0.05)
                if len([f for f in ws.sent_frames() if f.get("type") == "subscribe"]) > initial_frames:
                    break
            retry_frames = [f for f in ws.sent_frames() if f.get("type") == "subscribe"]
            assert len(retry_frames) == initial_frames + 1
            ids = {entry["id"] for entry in retry_frames[-1]["ids"]}
            assert ids == {"abc-region-a", "def-region-a"}
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            ws.inject(
                {"type": "snapshot", "id": "def-region-a", "status": "running", "version": 1, "error": None}
            )
            results = await asyncio.gather(first, second)
            assert [result.state for result in results] == ["started", "started"]
        await hub._close_hub()

    async def test_inbound_limit_retries_live_subscriptions(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            ws.inject({"type": "error", "id": None, "code": "LIMIT", "message": "inbound frame rate"})
            for _ in range(60):
                await asyncio.sleep(0.05)
                if len([f for f in ws.sent_frames() if f.get("type") == "subscribe"]) >= 2:
                    break
            subs = [f for f in ws.sent_frames() if f.get("type") == "subscribe"]
            assert len(subs) >= 2, subs
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            assert (await asyncio.wait_for(task, timeout=5)).state == "started"
        await hub._close_hub()

    async def test_malformed_fails_the_waiter(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            ws.inject(
                {"type": "error", "id": "abc-region-a", "code": "MALFORMED", "message": "invalid sandbox id"}
            )
            with pytest.raises(DaytonaError):
                await asyncio.wait_for(task, timeout=5)

            # The optimistic local subscription is released: a later wait on
            # the same id must send a subscribe, not assume it already has one
            # and wait out its timeout.
            sent_before = len([f for f in ws.sent_frames() if f.get("type") == "subscribe"])
            again = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            await asyncio.sleep(0.06)
            assert len([f for f in ws.sent_frames() if f.get("type") == "subscribe"]) == sent_before + 1
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            assert (await asyncio.wait_for(again, timeout=5)).state == "started"
        await hub._close_hub()


@pytest.mark.anyio
class TestFreshSubscribeBatching:
    async def test_many_waits_share_one_subscribe_frame_per_flush(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._subscribe_flush_s = 0.01
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            bootstrap = asyncio.create_task(hub.wait("bootstrap-region-a", _started_predicate(), timeout=30))
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)
            await asyncio.sleep(0.02)
            ws.inject(
                {
                    "type": "snapshot",
                    "id": "bootstrap-region-a",
                    "status": "running",
                    "version": 1,
                    "error": None,
                }
            )
            assert (await asyncio.wait_for(bootstrap, timeout=5)).state == "started"
            initial_frames = len([frame for frame in ws.sent_frames() if frame.get("type") == "subscribe"])
            waits = [
                asyncio.create_task(hub.wait(f"id{index}-region-a", _started_predicate(), timeout=0))
                for index in range(300)
            ]
            await asyncio.wait_for(
                _wait_for(lambda: sum(len(waiters) for waiters in hub._waiters.values()) == 300),
                timeout=1.0,
            )
            await asyncio.sleep(0.02)
            subscribe_frames = [frame for frame in ws.sent_frames() if frame.get("type") == "subscribe"]
            assert len(subscribe_frames) == initial_frames + 1
            assert len(subscribe_frames[-1]["ids"]) == 300
            await hub._close_hub()
            await asyncio.gather(*waits, return_exceptions=True)

    async def test_two_thousand_five_hundred_waits_chunk_into_three_frames(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._subscribe_flush_s = 0.01
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            bootstrap = asyncio.create_task(hub.wait("bootstrap-region-a", _started_predicate(), timeout=30))
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)
            await asyncio.sleep(0.02)
            ws.inject(
                {
                    "type": "snapshot",
                    "id": "bootstrap-region-a",
                    "status": "running",
                    "version": 1,
                    "error": None,
                }
            )
            assert (await asyncio.wait_for(bootstrap, timeout=5)).state == "started"
            initial_frames = len([frame for frame in ws.sent_frames() if frame.get("type") == "subscribe"])
            waits = [
                asyncio.create_task(hub.wait(f"id{index}-region-a", _started_predicate(), timeout=0))
                for index in range(2_500)
            ]
            await asyncio.wait_for(
                _wait_for(lambda: sum(len(waiters) for waiters in hub._waiters.values()) == 2_500),
                timeout=1.0,
            )
            await asyncio.sleep(0.02)
            subscribe_frames = [frame for frame in ws.sent_frames() if frame.get("type") == "subscribe"]
            assert [len(frame["ids"]) for frame in subscribe_frames[initial_frames:]] == [1000, 1000, 500]
            await hub._close_hub()
            await asyncio.gather(*waits, return_exceptions=True)

    async def test_an_id_subscribed_and_released_within_one_flush_window_sends_neither_frame(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._subscribe_flush_s = 0.05
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            bootstrap = asyncio.create_task(hub.wait("bootstrap-region-a", _started_predicate(), timeout=30))
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)
            await asyncio.sleep(0.03)
            ws.inject(
                {
                    "type": "snapshot",
                    "id": "bootstrap-region-a",
                    "status": "running",
                    "version": 1,
                    "error": None,
                }
            )
            assert (await asyncio.wait_for(bootstrap, timeout=5)).state == "started"
            await asyncio.sleep(0.06)
            sent_before = len([frame for frame in ws.sent_frames() if frame.get("type") == "subscribe"])
            unsub_before = len([frame for frame in ws.sent_frames() if frame.get("type") == "unsubscribe"])
            wait = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=0.01))
            await asyncio.wait_for(
                _wait_for(lambda: sum(len(waiters) for waiters in hub._waiters.values()) == 1),
                timeout=1.0,
            )
            with pytest.raises(RequestTimeoutError):
                await asyncio.wait_for(wait, timeout=1)
            await asyncio.sleep(0.06)
            assert (
                len([frame for frame in ws.sent_frames() if frame.get("type") == "subscribe"]) == sent_before
            )
            assert (
                len([frame for frame in ws.sent_frames() if frame.get("type") == "unsubscribe"])
                == unsub_before
            )
        await hub._close_hub()

    async def _held_and_released(self, hub: WaitHub, fc: FakeConnector, ws: FakeWS) -> None:
        """Bootstrap a socket, then hold abc-region-a on the server and release it.

        After this the id's unsubscribe is pending in the current flush window
        and the server still holds the id."""
        bootstrap = asyncio.create_task(hub.wait("bootstrap-region-a", _started_predicate(), timeout=30))
        await drain()
        await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)
        await asyncio.sleep(0.03)
        ws.inject(
            {"type": "snapshot", "id": "bootstrap-region-a", "status": "running", "version": 1, "error": None}
        )
        assert (await asyncio.wait_for(bootstrap, timeout=5)).state == "started"
        first = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
        await asyncio.wait_for(
            _wait_for(
                lambda: any(
                    f.get("type") == "subscribe" and any(i["id"] == "abc-region-a" for i in f["ids"])
                    for f in ws.sent_frames()
                )
            ),
            timeout=1.0,
        )
        ws.inject(
            {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
        )
        assert (await asyncio.wait_for(first, timeout=5)).state == "started"

    async def test_a_released_id_re_awaited_and_dropped_in_one_window_is_still_unsubscribed(self) -> None:
        # release -> re-await -> drop within one flush window: the re-await
        # cancels the pending unsubscribe, the drop cancels the pending
        # subscribe, but the server still holds the id from the first wait.
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._subscribe_flush_s = 0.05
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            await self._held_and_released(hub, fc, ws)
            again = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            again.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await again
            await asyncio.sleep(0.12)
            unsubscribed = [
                i
                for f in ws.sent_frames()
                if f.get("type") == "unsubscribe"
                for i in f["ids"]
                if i == "abc-region-a"
            ]
            assert unsubscribed == ["abc-region-a"], (
                "the server still held the id; it must be unsubscribed once"
            )
        await hub._close_hub()

    async def test_ids_enqueued_while_a_flush_is_sending_get_their_own_flush(self) -> None:
        # send() yields while the write buffer is above its high-water mark.
        # Ids enqueued during that await must not wait for an unrelated
        # later enqueue to be flushed.
        fc = FakeConnector()
        hub = make_hub(fc)
        hub._subscribe_flush_s = 0.02
        ws = fc.prepare()
        gate = asyncio.Event()
        original_send = ws.send
        slow = {"on": False}

        async def send(data: str) -> None:
            if slow["on"] and json.loads(data).get("type") == "subscribe":
                slow["on"] = False
                await gate.wait()
            await original_send(data)

        ws.send = send  # type: ignore[method-assign]
        with respx.mock:
            ticket_mock("region-a")
            bootstrap = asyncio.create_task(hub.wait("bootstrap-region-a", _started_predicate(), timeout=30))
            await drain()
            await asyncio.wait_for(_wait_for(lambda: fc.connection_count() == 1), timeout=1.0)
            await asyncio.sleep(0.03)
            ws.inject(
                {
                    "type": "snapshot",
                    "id": "bootstrap-region-a",
                    "status": "running",
                    "version": 1,
                    "error": None,
                }
            )
            assert (await asyncio.wait_for(bootstrap, timeout=5)).state == "started"

            slow["on"] = True
            first = asyncio.create_task(hub.wait("one-region-a", _started_predicate(), timeout=30))
            await asyncio.sleep(0.05)  # the flush is now blocked inside send()
            second = asyncio.create_task(hub.wait("two-region-a", _started_predicate(), timeout=30))
            await asyncio.sleep(0.01)
            gate.set()
            await asyncio.wait_for(
                _wait_for(
                    lambda: any(
                        f.get("type") == "subscribe" and any(i["id"] == "two-region-a" for i in f["ids"])
                        for f in ws.sent_frames()
                    )
                ),
                timeout=1.0,
            )
            first.cancel()
            second.cancel()
            for task in (first, second):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await hub._close_hub()


@pytest.mark.anyio
class TestCredentialRefusal:
    """A `4401` means "mint a fresh ticket" — once. If the mint keeps
    succeeding and the socket keeps being refused, minting again as fast as
    the loop allows is a hot loop against the API."""

    async def test_repeated_4401_fails_the_waiters_instead_of_spinning(self) -> None:
        import websockets.exceptions as ws_exc
        import websockets.frames as ws_frames

        fc = FakeConnector()
        hub = make_hub(fc)
        with respx.mock:
            ticket_mock("region-a")
            for _ in range(6):
                fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            seen: set[int] = set()
            for _ in range(40):
                if task.done():
                    break
                await drain()
                # Every socket the hub opens is closed 4401 by the server.
                # The hub backs off between attempts, so give real time.
                if fc.connection_count() > 0 and id(fc.last) not in seen:
                    seen.add(id(fc.last))
                    fc.last.inject_close(
                        ws_exc.ConnectionClosedError(
                            ws_frames.Close(4401, "credential no longer valid"), None
                        )
                    )
                await asyncio.sleep(0.1)
            with pytest.raises(AuthenticationError):
                await asyncio.wait_for(task, timeout=5)
            # Exactly one connection per refusal, and the last one ends the
            # loop: "mint a fresh ticket — once, then give up". A `<=` here
            # would also pass if the hub gave up on the FIRST 4401.
            assert fc.connection_count() == _MAX_CREDENTIAL_REFUSALS
        await hub._close_hub()

    async def test_frames_reset_credential_refusals(self) -> None:
        import websockets.exceptions as ws_exc
        import websockets.frames as ws_frames

        fc = FakeConnector()
        hub = make_hub(fc)
        with respx.mock:
            ticket_mock("region-a")
            ws1 = fc.prepare()
            ws2 = fc.prepare()
            ws3 = fc.prepare()
            ws4 = fc.prepare()
            waiting = asyncio.create_task(
                hub.wait(
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"paused"}),
                        terminal_states=frozenset({"destroying", "destroyed", "error"}),
                    ),
                    timeout=10,
                )
            )
            await drain()

            ws1.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            ws1.inject({"type": "ping"})
            ws1.inject_close(ws_exc.ConnectionClosedError(ws_frames.Close(4401, "credential expired"), None))

            await asyncio.sleep(0.2)
            await drain()
            ws2.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 2, "error": None}
            )
            ws2.inject({"type": "ping"})
            ws2.inject_close(ws_exc.ConnectionClosedError(ws_frames.Close(4401, "credential expired"), None))

            await asyncio.sleep(0.2)
            await drain()
            ws3.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 3, "error": None}
            )
            ws3.inject({"type": "ping"})
            ws3.inject_close(ws_exc.ConnectionClosedError(ws_frames.Close(4401, "credential expired"), None))

            await asyncio.sleep(0.2)
            await drain()
            ws4.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "paused", "version": 4, "error": None}
            )

            outcome = await asyncio.wait_for(waiting, timeout=5)
            assert outcome.state == "paused"
            assert outcome.raw_state == "paused"
            assert fc.connection_count() == 4
        await hub._close_hub()

    async def test_post_open_failures_back_off_before_reconnecting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import websockets.exceptions as ws_exc
        import websockets.frames as ws_frames

        import daytona.v2._events as events_module

        fc = FakeConnector()
        hub = make_hub(fc)
        mint_calls = 0

        def mint_response(request: httpx.Request) -> httpx.Response:
            nonlocal mint_calls
            mint_calls += 1
            return httpx.Response(
                200,
                json={"ticket": "t0k3n", "expires_in_ms": 30000, "subprotocol": "daytona.events.v1"},
            )

        monkeypatch.setattr(events_module, "_backoff", lambda attempt: 0.1 if attempt == 0 else 0.2)

        with respx.mock:
            respx.post(f"{_API_URL}/events/region-a/ticket").mock(side_effect=mint_response)
            ws1 = fc.prepare()
            ws2 = fc.prepare()
            ws3 = fc.prepare()
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=30))
            await drain()
            assert mint_calls == 1

            ws1.inject_close(ws_exc.ConnectionClosedError(ws_frames.Close(1013, "try again later"), None))
            await asyncio.sleep(0.05)
            await drain()
            assert mint_calls == 1
            await asyncio.sleep(0.08)
            await drain()
            assert mint_calls == 2

            ws2.inject_close(ConnectionError("transport failed"))
            await asyncio.sleep(0.15)
            await drain()
            assert mint_calls == 2
            await asyncio.wait_for(_wait_for(lambda: mint_calls == 3), timeout=1.0)
            assert mint_calls == 3

            ws3.inject(
                {"type": "event", "id": "abc-region-a", "status": "running", "version": 3, "error": None}
            )
            assert (await asyncio.wait_for(task, timeout=5)).state == "started"

        await hub._close_hub()


@pytest.mark.anyio
class TestIdleClose:
    """Sockets without waiters stay open unless ``idle_close`` is configured."""

    async def test_socket_stays_open_by_default(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await task
            await asyncio.sleep(0.05)
            assert not ws.closed
        await hub._close_hub()

    async def test_idle_close_closes_after_the_last_waiter(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc, idle_close=0.02)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            task = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await task
            assert not ws.closed
            for _ in range(50):
                await asyncio.sleep(0.01)
                if ws.closed:
                    break
            assert ws.closed
        await hub._close_hub()

    async def test_a_new_waiter_keeps_the_socket(self) -> None:
        fc = FakeConnector()
        hub = make_hub(fc, idle_close=0.05)
        ws = fc.prepare()
        with respx.mock:
            ticket_mock("region-a")
            first = asyncio.create_task(hub.wait("abc-region-a", _started_predicate(), timeout=5))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await first
            second = asyncio.create_task(hub.wait("other-region-a", _started_predicate(), timeout=5))
            await drain()
            await asyncio.sleep(0.09)
            assert not ws.closed
            ws.inject(
                {"type": "snapshot", "id": "other-region-a", "status": "running", "version": 1, "error": None}
            )
            await second
        await hub._close_hub()


@pytest.mark.anyio
class TestEndpointLearnedThroughTheTransport:
    """The hub learns the event endpoint from a response header on non-GET responses,
    through the transport every public client actually runs on. The re-keying
    tests above call `hub.learn()` directly, which is why they never noticed
    the async transport ignoring the callback."""

    async def test_a_lifecycle_mutation_teaches_the_hub_the_endpoint(self) -> None:
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        client = AsyncDaytona(DaytonaConfig(api_key="key"))
        sandbox = lazy_sandbox(client, "abc-region-a")
        sandbox.sandbox_class = "macos"
        sandbox.persistent = True
        with respx.mock:
            respx.post(f"{_API_URL}/sandboxes/abc-region-a/pause").mock(
                return_value=httpx.Response(200, json={}, headers={"x-daytona-cell": "endpoint-a"})
            )
            respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        **_sandbox_detail_payload("abc-region-a", "paused"),
                        "class": "macos",
                        "persistent": True,
                    },
                )
            )
            await sandbox.pause()
        hub = client._acquire_hub()
        assert hub is not None
        assert hub._learned.get("region-a") == "endpoint-a"
        await client.close()

    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({"x-daytona-cell": "endpoint-new"}, "endpoint-new"),
            ({"x-unrelated-header": "other-value"}, None),
            ({"x-daytona-cell": "endpoint-new", "x-unrelated-header": "other-value"}, "endpoint-new"),
        ],
        ids=["current", "other-header-ignored", "both"],
    )
    async def test_only_the_endpoint_header_teaches_the_hub(
        self, headers: Dict[str, str], expected: Optional[str]
    ) -> None:
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        client = AsyncDaytona(DaytonaConfig(api_key="key"))
        sandbox = lazy_sandbox(client, "abc-region-a")
        sandbox.sandbox_class = "macos"
        sandbox.persistent = True
        with respx.mock:
            respx.post(f"{_API_URL}/sandboxes/abc-region-a/pause").mock(
                return_value=httpx.Response(200, json={}, headers=headers)
            )
            respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        **_sandbox_detail_payload("abc-region-a", "paused"),
                        "class": "macos",
                        "persistent": True,
                    },
                )
            )
            await sandbox.pause()
        hub = client._acquire_hub()
        assert hub is not None
        assert hub._learned.get("region-a") == expected
        await client.close()

    async def test_a_failed_request_teaches_nothing(self) -> None:
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        client = AsyncDaytona(DaytonaConfig(api_key="key"))
        sandbox = lazy_sandbox(client, "abc-region-a")
        sandbox.sandbox_class = "macos"
        sandbox.persistent = True
        with respx.mock:
            respx.post(f"{_API_URL}/sandboxes/abc-region-a/pause").mock(
                return_value=httpx.Response(
                    409, json={"code": "CONFLICT", "message": "no"}, headers={"x-daytona-cell": "endpoint-a"}
                )
            )
            with pytest.raises(DaytonaError):
                await sandbox.pause()
        hub = client._acquire_hub()
        assert hub is not None
        assert "region-a" not in hub._learned
        await client.close()

    async def test_create_followed_by_wait_uses_the_socket_not_long_poll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        seen_queries: list[dict[str, str]] = []
        fc = FakeConnector()
        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
        monkeypatch.setattr(events_module, "_default_connector", fc)

        def sandbox_detail(request: httpx.Request) -> httpx.Response:
            seen_queries.append(dict(request.url.params.multi_items()))
            return httpx.Response(200, json=_sandbox_detail_payload("abc-region-a", "running"))

        client = AsyncDaytona(DaytonaConfig(api_key="key"))
        try:
            with respx.mock:
                respx.post(f"{_API_URL}/sandboxes").mock(
                    return_value=httpx.Response(
                        202,
                        json={"id": "abc-region-a", "status": "pending", "wait_cap_seconds": 0},
                        headers={"x-daytona-cell": "endpoint-a"},
                    )
                )
                respx.post(f"{_API_URL}/events/region-a/ticket").mock(
                    return_value=httpx.Response(
                        200,
                        json={"ticket": "t0k3n", "expires_in_ms": 30000, "subprotocol": "daytona.events.v1"},
                    )
                )
                respx.get(f"{_API_URL}/sandboxes/abc-region-a").mock(side_effect=sandbox_detail)
                ws = fc.prepare()
                sandbox = await client.create("python:3.12", wait_seconds=0)
                task = asyncio.create_task(sandbox.wait(timeout=5))
                await drain()
                ws.inject(
                    {
                        "type": "snapshot",
                        "id": "abc-region-a",
                        "status": "running",
                        "version": 1,
                        "error": None,
                    }
                )
                await asyncio.wait_for(task, timeout=1.0)

            assert fc.connection_count() == 1
            # The subprotocol and ticket names.
            assert fc.last_subprotocols == ["daytona.events.v1", "daytona.ticket.t0k3n"]
            assert seen_queries == [{}]
            assert client._acquire_hub()._learned.get("region-a") == "endpoint-a"  # type: ignore[union-attr]
        finally:
            await client.close()

    async def test_not_found_known_false_404s_fast_via_one_poll_for_bounded_and_unbounded_waits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
        for timeout in (3, None):
            fc = FakeConnector()
            monkeypatch.setattr(events_module, "_default_connector", fc)
            client = AsyncDaytona(DaytonaConfig(api_key=_API_KEY))
            api = RecordingTransport([create_error("gone", 404, "NOT_FOUND", "DAYTONA_API")])
            bind_api(client, api)
            sandbox = lazy_sandbox(client, "abc-region-a")
            with respx.mock(assert_all_called=False):
                ticket_mock("region-a")
                ws = fc.prepare()
                task = asyncio.create_task(sandbox.wait_for_state(["started"], timeout=timeout))
                await drain()
                ws.inject({"type": "error", "id": "abc-region-a", "code": "NOT_FOUND", "was_known": False})
                with pytest.raises(NotFoundError):
                    await asyncio.wait_for(task, timeout=1)
                assert len(api.specs) == 1
            await client.close()

    async def test_wait_after_close_polls_without_minting_a_new_ticket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        fc = FakeConnector()
        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
        monkeypatch.setattr(events_module, "_default_connector", fc)

        client = AsyncDaytona(DaytonaConfig(api_key=_API_KEY))
        api = RecordingTransport([_sandbox_detail_payload("abc-region-a", "running")])
        bind_api(client, api)
        sandbox = lazy_sandbox(client, "abc-region-a")

        async def no_close() -> None:
            return None

        client._api.close = no_close  # type: ignore[method-assign]
        client._toolbox.close = no_close  # type: ignore[method-assign]
        await client.close()

        with respx.mock:
            ticket = ticket_mock("region-a")
            ws = fc.prepare()
            task = asyncio.create_task(sandbox.wait_for_state(["started"], timeout=3))
            await drain()
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "running", "version": 1, "error": None}
            )
            await asyncio.wait_for(task, timeout=1)

        assert ticket.call_count == 0
        assert len(api.specs) == 1
        assert fc.connection_count() == 0


class TestSyncSurfaceSharesTheHub:
    """A handle made on the sync client runs on the caller's thread, where no event
    loop runs. Attaching the hub there made it PRIVATE (keyed to no loop) and
    raced between threads: 20 concurrent cold calls opened 20 hubs. The hub is
    now attached on first use, on the bridge loop."""

    def test_handles_made_on_many_threads_share_one_hub(self) -> None:
        import concurrent.futures

        from daytona.v2._sync import Daytona
        from daytona.v2.types import DaytonaConfig

        client = Daytona(DaytonaConfig(api_key="key"))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                handles = list(pool.map(lambda i: lazy_sandbox(client, f"abc{i}-region-a"), range(20)))
            # Nothing is attached at construction off the loop...
            assert all(h._impl._hub is None for h in handles)
            # ...and on first use every handle resolves the SAME hub, the one
            # the bridged surface uses.
            hubs = {id(client._bridge.call(lambda h=h: _resolve(h._impl))) for h in handles}
            assert len(hubs) == 1
            assert hubs == {id(client._bridge.call(lambda: _resolve_client(client._impl)))}
        finally:
            client.close()


@pytest.mark.anyio
class TestPublicWaitForStateProbeBehavior:
    async def test_wait_for_state_failed_probe_retries_at_about_one_probe_per_early_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        class FailingTransport:
            def __init__(self) -> None:
                self.specs: list[object] = []

            async def send(self, spec: object) -> object:
                self.specs.append(spec)
                raise create_error("busy", 503, "SERVICE_UNAVAILABLE", "DAYTONA_API")

            async def close(self) -> None:
                return None

        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
        fc = FakeConnector()
        monkeypatch.setattr(events_module, "_default_connector", fc)
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        client = AsyncDaytona(DaytonaConfig(api_key=_API_KEY))
        api = FailingTransport()
        bind_api(client, api)  # type: ignore[arg-type]
        client._acquire_hub()._early_probe_s = 0.04  # type: ignore[union-attr]
        sandbox = lazy_sandbox(client, "abc-region-a")

        try:
            with respx.mock:
                ticket_mock("region-a")
                fc.prepare()
                with pytest.raises(RequestTimeoutError):
                    await asyncio.wait_for(
                        sandbox.wait_for_state(["started"], timeout=0.15),
                        timeout=1.0,
                    )
            assert len(api.specs) <= 3
        finally:
            await client.close()

    async def test_wait_for_state_short_budget_probe_still_requires_socket_silence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        class RecordingTransport:
            def __init__(self) -> None:
                self.specs: list[object] = []

            async def send(self, spec: object) -> object:
                self.specs.append(spec)
                return _sandbox_detail_payload("abc-region-a", "running")

            async def close(self) -> None:
                return None

        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
        fc = FakeConnector()
        monkeypatch.setattr(events_module, "_default_connector", fc)
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        client = AsyncDaytona(DaytonaConfig(api_key=_API_KEY))
        api = RecordingTransport()
        bind_api(client, api)  # type: ignore[arg-type]
        client._acquire_hub()._early_probe_s = 0.08  # type: ignore[union-attr]
        sandbox = lazy_sandbox(client, "abc-region-a")

        try:
            with respx.mock:
                ticket_mock("region-a")
                ws = fc.prepare()
                task = asyncio.create_task(sandbox.wait_for_state(["started"], timeout=0.1))
                await drain()
                await asyncio.sleep(0.02)
                ws.inject(
                    {
                        "type": "snapshot",
                        "id": "abc-region-a",
                        "status": "pending",
                        "version": 1,
                        "error": None,
                    }
                )
                await asyncio.sleep(0.03)
                await drain()
                assert not task.done()
                assert api.specs == []
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        finally:
            await client.close()

    async def test_wait_for_state_resolves_within_budget_when_probe_sees_not_ready_then_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import daytona.v2._events as events_module
        import daytona.v2.sandbox as sandbox_module
        from daytona.v2.daytona import AsyncDaytona
        from daytona.v2.types import DaytonaConfig

        class RecordingTransport:
            def __init__(self) -> None:
                self.specs: list[object] = []
                self.started = events_module._clock.monotonic()
                self.probe_at: list[float] = []

            async def send(self, spec: object) -> object:
                self.specs.append(spec)
                self.probe_at.append(events_module._clock.monotonic())
                status = "running" if self.probe_at[-1] - self.started >= 2.3 else "booting"
                return _sandbox_detail_payload("abc-region-a", status)

            async def close(self) -> None:
                return None

        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
        fc = FakeConnector()
        monkeypatch.setattr(events_module, "_default_connector", fc)
        monkeypatch.setattr(events_module, "_jittered", lambda value, fraction=0.1: value)
        original_sleep = events_module._sleep
        recorded_sleeps: list[float] = []

        async def recording_sleep(delay: float) -> None:
            recorded_sleeps.append(delay)
            await original_sleep(delay)

        monkeypatch.setattr(events_module, "_sleep", recording_sleep)
        client = AsyncDaytona(DaytonaConfig(api_key=_API_KEY))
        api = RecordingTransport()
        bind_api(client, api)  # type: ignore[arg-type]
        client._acquire_hub()._early_probe_s = 1.0  # type: ignore[union-attr]
        sandbox = lazy_sandbox(client, "abc-region-a")

        try:
            with respx.mock:
                ticket_mock("region-a")
                fc.prepare()
                await asyncio.wait_for(
                    sandbox.wait_for_state(["started", "stopped"], timeout=2.6),
                    timeout=3.5,
                )
            assert len(api.specs) >= 2
            assert all(
                later - earlier >= sandbox_module.WAIT_FOR_STATE_MIN_INTERVAL_S
                for earlier, later in zip(api.probe_at, api.probe_at[1:])
            )
            assert 0.0 not in recorded_sleeps
        finally:
            await client.close()


async def _resolve(sandbox: object) -> object:
    return sandbox._events_hub()  # type: ignore[attr-defined]


async def _resolve_client(impl: object) -> object:
    return impl._acquire_hub()  # type: ignore[attr-defined]
