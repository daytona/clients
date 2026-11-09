# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A terminal state the events hub reports is confirmed with one read, and the
read decides: a target state returns, a terminal state raises with the read
state, and any other state means the report was stale, so the wait polls on.

The hub can report a state it held before the new request's frames arrive: an
``error`` from an earlier wait, while a DELETE already moves the sandbox to
``destroying``. The TypeScript twin is ``tests/terminal-report.test.ts``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import pytest
import respx
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse
from test_core import _sandbox_detail_payload, bind_api
from test_events import _API_KEY, FakeConnector, drain, ticket_mock

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import SandboxTerminalStateError
from daytona.v2._events import WaitOutcome
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio


def _detail(status: str) -> JsonObject:
    return {
        "id": "sbx",
        "class": "linux",
        "name": "sbx",
        "status": status,
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
    }


_GONE = PlannedResponse(404, {"error": "sandbox not found", "code": "NOT_FOUND"})


class HeldErrorHub:
    """Reports the `error` it holds, before any frame of the new request."""

    enabled = True

    def __init__(self) -> None:
        self.calls = 0

    async def wait(self, sandbox_id: str, predicate: object, **kwargs: object) -> WaitOutcome:
        self.calls += 1
        raise SandboxTerminalStateError(sandbox_id, "error", raw_state="failed")


def _handle(root: AsyncDaytona, hub: HeldErrorHub) -> AsyncSandbox:
    sandbox = lazy_sandbox(root.v2, "sbx")
    sandbox._update(state="error", raw_state="failed")
    sandbox._hub = hub  # type: ignore[assignment]
    return sandbox


async def _run(
    monkeypatch: pytest.MonkeyPatch, responses: list[PlannedResponse], until: str = ""
) -> tuple[AsyncSandbox, list[tuple[str, str]], HeldErrorHub]:
    hub = HeldErrorHub()
    with FakeApi(responses) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = _handle(root, hub)
            if until:
                await sandbox.wait(until=until, timeout=10)  # type: ignore[arg-type]
            else:
                await sandbox.delete(timeout=10)
    return sandbox, [(request.method, request.path.split("?")[0]) for request in fake.requests], hub


async def test_a_held_error_then_a_404_read_is_a_finished_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox, requests, hub = await _run(monkeypatch, [PlannedResponse(200, {}), _GONE])
    assert hub.calls == 1
    assert requests == [("DELETE", "/sandboxes/sbx"), ("GET", "/sandboxes/sbx")]
    assert (sandbox.state, sandbox.raw_state) == ("destroyed", "deleted")


@pytest.mark.parametrize("gone", [PlannedResponse(200, _detail("deleted")), _GONE], ids=["deleted", "404"])
async def test_a_held_error_then_destroying_polls_on_until_the_sandbox_is_gone(
    monkeypatch: pytest.MonkeyPatch, gone: PlannedResponse
) -> None:
    sandbox, requests, _ = await _run(
        monkeypatch, [PlannedResponse(200, {}), PlannedResponse(200, _detail("deleting")), gone]
    )
    assert requests == [("DELETE", "/sandboxes/sbx"), ("GET", "/sandboxes/sbx"), ("GET", "/sandboxes/sbx")]
    assert sandbox.state == "destroyed"


async def test_a_held_error_confirmed_by_the_read_raises_with_the_read_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SandboxTerminalStateError) as raised:
        await _run(monkeypatch, [PlannedResponse(200, {}), PlannedResponse(200, _detail("failed"))])
    assert (raised.value.state, raised.value.raw_state) == ("error", "failed")


async def test_a_held_error_read_as_another_terminal_state_raises_with_the_read_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hub reports `error`; the read finds `deleting` (`destroying`, terminal
    # for a wait for `started`): the read decides, so the error and the handle
    # carry `destroying`, not the reported `error`.
    hub = HeldErrorHub()
    with FakeApi([PlannedResponse(200, _detail("deleting"))]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = _handle(root, hub)
            with pytest.raises(SandboxTerminalStateError) as raised:
                await sandbox.wait(until="started", timeout=10)
    assert [(request.method, request.path.split("?")[0]) for request in fake.requests] == [
        ("GET", "/sandboxes/sbx")
    ]
    assert (raised.value.state, raised.value.raw_state) == ("destroying", "deleting")
    assert (sandbox.state, sandbox.raw_state) == ("destroying", "deleting")


async def test_a_held_terminal_state_then_starting_polls_on_until_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox, requests, _ = await _run(
        monkeypatch,
        [PlannedResponse(200, _detail("booting")), PlannedResponse(200, _detail("running"))],
        until="started",
    )
    assert requests == [("GET", "/sandboxes/sbx"), ("GET", "/sandboxes/sbx")]
    assert sandbox.state == "started"


async def test_a_failed_read_keeps_the_reported_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # A read that fails (here a 500) cannot overturn the report: it raises as before.
    with pytest.raises(SandboxTerminalStateError) as raised:
        await _run(monkeypatch, [PlannedResponse(200, {}), PlannedResponse(500, {"message": "stop"})])
    assert raised.value.state == "error"


async def test_a_404_read_outside_a_delete_keeps_the_reported_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SandboxTerminalStateError) as raised:
        await _run(monkeypatch, [_GONE], until="started")
    assert raised.value.state == "error"


async def test_a_stale_error_snapshot_on_the_real_hub_ends_the_delete_once_the_sandbox_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real hub: delete() sends the DELETE, subscribes, and the snapshot
    # still says `failed` (taken before the DELETE moved the sandbox on). The
    # hub reports `error` at once; the read finds `deleting`, so the wait polls
    # on to the 404. (A subscription is released when its last waiter ends,
    # and every waiter treats `error` as terminal, so a held `error` reaches a
    # new wait through a snapshot like this one.)
    import daytona.v2._events as events_module
    from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
    from daytona.v2.errors import create_error
    from daytona.v2.types import DaytonaConfig as DaytonaConfigV2

    class ScriptedTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.reads = ["deleting", "gone"]

        async def send(self, spec: object) -> object:
            method, path = spec.method, spec.path
            self.calls.append((method, path))
            if method == "DELETE":
                return {}
            state = self.reads.pop(0)
            if state == "gone":
                raise create_error("sandbox not found", 404, "NOT_FOUND", "DAYTONA_API")
            return _sandbox_detail_payload("abc-region-a", state)

        async def close(self) -> None:
            return None

    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.test")
    connector = FakeConnector()
    monkeypatch.setattr(events_module, "_default_connector", connector)
    client = AsyncDaytonaV2(DaytonaConfigV2(api_key=_API_KEY))
    api = ScriptedTransport()
    bind_api(client, api)  # type: ignore[arg-type]
    sandbox = lazy_sandbox(client, "abc-region-a")
    try:
        with respx.mock:
            ticket_mock("region-a")
            ws = connector.prepare()
            deleting = asyncio.create_task(sandbox.delete(timeout=5))
            await drain()
            await asyncio.wait_for(_until(lambda: connector.connection_count() == 1), timeout=1)
            await asyncio.wait_for(
                _until(lambda: any(f.get("type") == "subscribe" for f in ws.sent_frames())), timeout=1
            )
            ws.inject(
                {"type": "snapshot", "id": "abc-region-a", "status": "failed", "version": 1, "error": "boom"}
            )
            await asyncio.wait_for(deleting, timeout=10)
        assert api.calls == [
            ("DELETE", "/sandboxes/abc-region-a"),
            ("GET", "/sandboxes/abc-region-a"),
            ("GET", "/sandboxes/abc-region-a"),
        ]
        assert (sandbox.state, sandbox.raw_state) == ("destroyed", "deleted")
    finally:
        await client.close()


async def _until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)


# Every terminal error raised from a read of the sandbox carries that read's
# wire status as `raw_state` and the server's explanation as `reason`.


class _PollingHub:
    """A hub that decides from its poll function, as the real hub's fallback does."""

    enabled = True

    async def wait(self, sandbox_id: str, predicate: object, **kwargs: object) -> WaitOutcome:
        from daytona.v2._events import _Predicate, _run_poll_loop

        poll = kwargs["sandbox_poll_fn"]
        assert isinstance(predicate, _Predicate) and callable(poll)
        return await _run_poll_loop(sandbox_id, predicate, deadline=None, fn=poll)


@pytest.mark.parametrize("hub", ["off", "on"])
async def test_a_polled_terminal_state_carries_its_wire_status_and_reason(
    monkeypatch: pytest.MonkeyPatch, hub: str
) -> None:
    failed = _detail("failed")
    failed["error"] = "why"
    with FakeApi([PlannedResponse(200, failed)]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "0")
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            if hub == "on":
                sandbox._hub = _PollingHub()  # type: ignore[assignment]
            with pytest.raises(SandboxTerminalStateError) as raised:
                await sandbox.wait(until="started", timeout=5)
    assert len(fake.requests) == 1
    assert (raised.value.state, raised.value.raw_state, raised.value.reason) == ("error", "failed", "why")
    assert str(raised.value).endswith(": why")
    assert (sandbox.state, sandbox.raw_state) == ("error", "failed")


async def test_a_held_failed_report_confirmed_by_a_read_with_a_reason_raises_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = _detail("failed")
    failed["error"] = "deletion did not finish"
    with pytest.raises(SandboxTerminalStateError) as raised:
        await _run(monkeypatch, [PlannedResponse(200, {}), PlannedResponse(200, failed)])
    assert (raised.value.state, raised.value.raw_state, raised.value.reason) == (
        "error",
        "failed",
        "deletion did not finish",
    )


@pytest.mark.parametrize(("read", "reported"), [("lost", "failed"), ("failed", "lost")])
async def test_a_reason_read_for_one_wire_status_never_labels_the_other(
    monkeypatch: pytest.MonkeyPatch, read: str, reported: str
) -> None:
    # The state came from a full read (the hub's poll), so no confirming read
    # follows; the handle's reason belongs to `read`, not to the `reported`
    # status, so the error carries none.
    class _ReportingHub:
        enabled = True

        async def wait(self, sandbox_id: str, predicate: object, **kwargs: object) -> WaitOutcome:
            poll = kwargs["sandbox_poll_fn"]
            assert callable(poll)
            await poll()
            raise SandboxTerminalStateError(sandbox_id, "error", raw_state=reported)

    detail = _detail(read)
    detail["error"] = f"{read} reason"
    with FakeApi([PlannedResponse(200, detail)]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            sandbox._hub = _ReportingHub()  # type: ignore[assignment]
            with pytest.raises(SandboxTerminalStateError) as raised:
                await sandbox.wait(until="started", timeout=5)
    assert len(fake.requests) == 1
    assert (raised.value.raw_state, raised.value.reason) == (reported, None)


# The hub's held state: a wait that rejects from a state the hub already holds
# for the sandbox carries the wire status the hub holds with it.


async def _hold(state: str) -> tuple[object, object, asyncio.Task[WaitOutcome]]:
    from test_events import make_hub

    from daytona.v2._events import _TargetPredicate

    connector = FakeConnector()
    hub = make_hub(connector)
    ws = connector.prepare()
    # A waiter that treats nothing as terminal keeps the subscription, and so
    # the state the hub holds, alive.
    keeper = asyncio.create_task(
        hub.wait(
            "abc-region-a",
            _TargetPredicate(states=frozenset({"started"}), terminal_states=frozenset()),
            timeout=30,
        )
    )
    await drain()
    ws.inject({"type": "snapshot", "id": "abc-region-a", "status": state, "version": 1, "error": None})
    await asyncio.wait_for(
        _until(lambda: any(sock.states.get("abc-region-a") == "error" for sock in hub._sockets.values())),
        timeout=1,
    )
    return hub, ws, keeper


@pytest.mark.parametrize("state", ["failed", "lost"])
async def test_a_wait_rejected_from_a_held_state_carries_its_wire_status(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    from daytona.v2._events import _TargetPredicate

    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")

    with respx.mock:
        ticket_mock("region-a")
        hub, _ws, keeper = await _hold(state)
        try:
            with pytest.raises(SandboxTerminalStateError) as raised:
                await hub.wait(  # type: ignore[attr-defined]
                    "abc-region-a",
                    _TargetPredicate(
                        states=frozenset({"started"}),
                        terminal_states=frozenset({"destroying", "destroyed", "error"}),
                    ),
                    timeout=5,
                )
            assert (raised.value.state, raised.value.raw_state) == ("error", state)
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
            await hub._close_hub()  # type: ignore[attr-defined]


async def test_a_held_terminal_state_keeps_its_wire_status_when_the_confirming_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
    from daytona.v2.errors import create_error
    from daytona.v2.types import DaytonaConfig as DaytonaConfigV2

    class FailingTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def send(self, spec: object) -> object:
            self.calls.append(spec.method)
            raise create_error("busy", 503, "SERVICE_UNAVAILABLE", "DAYTONA_API")

        async def close(self) -> None:
            return None

    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.test")
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
    client = AsyncDaytonaV2(DaytonaConfigV2(api_key=_API_KEY))
    api = FailingTransport()
    bind_api(client, api)  # type: ignore[arg-type]
    with respx.mock:
        ticket_mock("region-a")
        hub, _ws, keeper = await _hold("lost")
        try:
            sandbox = lazy_sandbox(client, "abc-region-a")
            sandbox._hub = hub  # type: ignore[assignment]
            with pytest.raises(SandboxTerminalStateError) as raised:
                await sandbox.wait(until="started", timeout=5)
            # The confirming read was made, failed, and the held report stands.
            assert api.calls == ["GET"]
            assert (raised.value.state, raised.value.raw_state, raised.value.reason) == (
                "error",
                "lost",
                None,
            )
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
            await hub._close_hub()  # type: ignore[attr-defined]
            await client.close()
