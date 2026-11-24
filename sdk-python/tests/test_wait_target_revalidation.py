# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Scripted state sequences for the sandbox waits.

A target state the events hub reports is followed by one read that fills in
the handle, and that read decides like any other: a target returns, a terminal
state raises, and any other state keeps the wait going under the same
deadline. ``start()`` that joins a start or resume another caller sent ends
when that operation fails. The TypeScript twin is
``tests/wait-target-revalidation.test.ts``; Go holds the same rules in
``sandbox_wait_revalidation_test.go``.
"""

from __future__ import annotations

from typing import Callable, Optional

import pytest
from _handles import lazy_sandbox

from daytona.v2 import RequestTimeoutError, SandboxTerminalStateError
from daytona.v2._body_limit import DEFAULT_MAX_RESPONSE_BYTES
from daytona.v2._core import RequestSpec
from daytona.v2._events import WaitOutcome, _evaluate, _TargetPredicate
from daytona.v2._sync import Daytona
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.errors import create_error
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import DaytonaConfig

pytestmark = pytest.mark.anyio

_PUBLIC = {
    "pending": "creating",
    "booting": "starting",
    "running": "started",
    "stopped": "stopped",
    "paused": "paused",
    "failed": "error",
}


class _Api:
    """Answers each GET with the next scripted status; the last one repeats."""

    request_timeout_seconds = 30.0
    max_response_bytes = DEFAULT_MAX_RESPONSE_BYTES
    _source = "sdk-python-async"

    def __init__(self, statuses: list[object], *, on_read: Optional[Callable[[], None]] = None) -> None:
        self.statuses = statuses
        self.on_read = on_read
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        if spec.method != "GET":
            return {}
        if self.on_read is not None:
            self.on_read()
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(status, Exception):
            raise status
        assert isinstance(status, str)
        return _detail(status)

    def reads(self) -> list[RequestSpec]:
        return [spec for spec in self.specs if spec.method == "GET"]

    async def close(self) -> None:
        pass


def _detail(status: str) -> dict[str, object]:
    detail: dict[str, object] = {
        "class": "linux",
        "cpu": 1,
        "cores_max": 1,
        "id": "sbx",
        "memory_mib": 1024,
        "disk_mib": 2048,
        "name": "sbx",
        "port_map": [],
        "region_id": "region-a",
        "status": status,
        "toolbox_proxy_url": "https://proxy.example",
    }
    if status == "failed":
        detail["error"] = "the guest crashed"
    return detail


class _ScriptedHub:
    """Delivers the scripted frames in order, deciding as the real hub does.

    A frame that resolves the predicate returns, one that rejects it raises
    the terminal error, and a script that decides nothing ends the hub's share
    of the budget (the SDK timeout the wait answers with its final reads).
    """

    enabled = True

    def __init__(self, *frames: str) -> None:
        self.frames = [WaitOutcome(_PUBLIC[raw], raw) for raw in frames]  # type: ignore[arg-type]
        self.terminal_states: frozenset[str] = frozenset()

    async def wait(
        self,
        sandbox_id: str,
        predicate: _TargetPredicate,
        *,
        on_observed: Callable[[WaitOutcome], None],
        **_options: object,
    ) -> WaitOutcome:
        self.terminal_states = frozenset(predicate.terminal_states)
        for frame in self.frames:
            decision = _evaluate(predicate, frame.state, sandbox_id)
            if decision == "resolve":
                return frame
            if decision == "reject":
                raise SandboxTerminalStateError(sandbox_id, frame.state, raw_state=frame.raw_state)
            on_observed(frame)
        raise create_error(
            "sandbox failed to reach the requested state before timeout", 408, "REQUEST_TIMEOUT", "SDK"
        )


def _sandbox(api: _Api, hub: Optional[_ScriptedHub], state: str = "creating") -> AsyncSandbox:
    sandbox = lazy_sandbox(AsyncDaytona(DaytonaConfig(api_key="key")), "sbx")
    sandbox._update(sandbox_class="linux", state=state, raw_state="x")  # type: ignore[arg-type]
    sandbox._api = api  # type: ignore[attr-defined]
    if hub is not None:
        sandbox._hub = hub  # type: ignore[assignment]
    return sandbox


def _clock_steps(monkeypatch: pytest.MonkeyPatch, step: float) -> Callable[[], None]:
    """The SDK's clock moves ``step`` seconds on every read the fake serves."""
    from daytona.v2 import _clock

    real = _clock.monotonic
    offset = [0.0]

    def monotonic() -> float:
        return real() + offset[0]

    def advance() -> None:
        offset[0] += step

    monkeypatch.setattr("daytona.v2._clock.monotonic", monotonic)
    return advance


async def test_a_target_event_whose_read_disagrees_waits_for_the_target() -> None:
    api = _Api(["pending", "pending", "running"])
    sandbox = _sandbox(api, _ScriptedHub("running"))

    await sandbox.wait(until="started", timeout=30)

    assert (sandbox.state, sandbox.raw_state) == ("started", "running")
    reads = api.reads()
    assert len(reads) == 3
    # The first read fills in the handle; the ones after it are long polls for the target.
    assert reads[0].params == {}
    assert all(read.params.get("wait") == "running" for read in reads[1:])


async def test_a_target_event_whose_read_shows_a_terminal_state_raises_it() -> None:
    api = _Api(["failed"])
    sandbox = _sandbox(api, _ScriptedHub("running"))

    with pytest.raises(SandboxTerminalStateError) as raised:
        await sandbox.wait(until="started", timeout=30)

    assert (raised.value.state, raised.value.raw_state, raised.value.reason) == (
        "error",
        "failed",
        "the guest crashed",
    )
    assert len(api.reads()) == 1


async def test_a_target_event_whose_read_never_converges_times_out_on_the_same_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api(["pending"])
    api.on_read = _clock_steps(monkeypatch, 1.0)
    sandbox = _sandbox(api, _ScriptedHub("running"))

    with pytest.raises(RequestTimeoutError) as raised:
        await sandbox.wait(until="started", timeout=5)

    assert raised.value.source == "SDK"
    assert sandbox.state == "creating"
    # One read a second of a 5-second budget: the budget is not restarted.
    assert len(api.reads()) == 5


async def test_a_target_event_whose_read_fails_keeps_the_target() -> None:
    api = _Api([create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API")])
    sandbox = _sandbox(api, _ScriptedHub("running"))

    await sandbox.wait(until="started", timeout=30)

    assert (sandbox.state, sandbox.raw_state) == ("started", "running")
    assert len(api.reads()) == 1


@pytest.mark.parametrize(
    ("verb", "state", "statuses", "frame", "target"),
    [
        # start() reads the sandbox first; the read after the event lags behind it.
        ("start", "stopped", ["stopped", "booting", "running"], "running", "started"),
        ("resume", "paused", ["booting", "running"], "running", "started"),
        ("stop", "started", ["stopping", "stopped"], "stopped", "stopped"),
        ("pause", "started", ["pausing", "paused"], "paused", "paused"),
    ],
)
async def test_lifecycle_waits_revalidate_the_reported_target(
    verb: str, state: str, statuses: list[object], frame: str, target: str
) -> None:
    api = _Api(statuses)
    sandbox = _sandbox(api, _ScriptedHub(frame), state=state)

    await getattr(sandbox, verb)(timeout=30)

    assert sandbox.state == target
    # Every scripted read was served: the lagging read did not end the wait.
    assert api.statuses == [frame]


def test_the_sync_surface_revalidates_the_reported_target() -> None:
    api = _Api(["pending", "running"])
    with Daytona(DaytonaConfig(api_key="key")) as client:
        sandbox = lazy_sandbox(client, "sbx")
        sandbox._impl._update(sandbox_class="linux", state="creating", raw_state="pending")
        sandbox._impl._api = api  # type: ignore[attr-defined]
        sandbox._impl._hub = _ScriptedHub("running")  # type: ignore[assignment]

        sandbox.wait(until="started", timeout=30)

        assert sandbox.state == "started"
    assert len(api.reads()) == 2


@pytest.mark.parametrize("hub", [True, False], ids=["events", "polling"])
@pytest.mark.parametrize(("failed", "public"), [("stopped", "stopped"), ("paused", "paused")])
async def test_start_that_joins_a_start_raises_when_it_fails(hub: bool, failed: str, public: str) -> None:
    # The first read finds the sandbox starting: another caller sent the start
    # or resume. It fails, and the sandbox goes back to where it was.
    api = _Api(["booting", "booting", failed])
    scripted = _ScriptedHub("booting", failed) if hub else None
    sandbox = _sandbox(api, scripted, state="starting")

    with pytest.raises(SandboxTerminalStateError) as raised:
        await sandbox.start(timeout=30)

    assert (raised.value.state, raised.value.raw_state) == (public, failed)
    # Nothing is sent: the start is joined, never repeated.
    assert [spec.method for spec in api.specs] == ["GET"] * len(api.specs)
    if scripted is not None:
        assert {"stopped", "paused"} <= scripted.terminal_states


async def test_start_that_joins_a_start_returns_when_it_succeeds() -> None:
    api = _Api(["booting", "booting", "running"])
    sandbox = _sandbox(api, None, state="starting")

    await sandbox.start(timeout=30)

    assert sandbox.state == "started"
    assert [spec.method for spec in api.specs] == ["GET", "GET", "GET"]


async def test_start_of_a_stopped_sandbox_waits_through_a_read_that_still_shows_it_stopped() -> None:
    # The start is accepted, but the first read still shows the sandbox
    # stopped: that is no failure of the start this call sent.
    api = _Api(["stopped", "stopped", "booting", "running"])
    sandbox = _sandbox(api, None, state="stopped")

    await sandbox.start(timeout=30)

    assert sandbox.state == "started"
    assert [(spec.method, spec.path) for spec in api.specs][:2] == [
        ("GET", "/sandboxes/sbx"),
        ("POST", "/sandboxes/sbx/start"),
    ]


# A read can lag back to the state the sandbox was in before the operation,
# and for a pause or a joined start that state is also the operation's own
# failure state. Only long-poll reads decide it.
_PRE_OPERATION = [
    # (verb, handle state, reads before the event's read, frame, the stale state, target)
    ("pause", "started", [], "paused", "running", "paused"),
    ("start", "starting", ["booting"], "running", "stopped", "started"),
    ("start", "starting", ["booting"], "running", "paused", "started"),
]


@pytest.mark.parametrize(("verb", "state", "before", "frame", "stale", "target"), _PRE_OPERATION)
async def test_a_read_showing_the_state_before_the_operation_after_a_target_event_waits_for_the_target(
    verb: str, state: str, before: list[object], frame: str, stale: str, target: str
) -> None:
    api = _Api([*before, stale, frame])
    sandbox = _sandbox(api, _ScriptedHub(frame), state=state)

    await getattr(sandbox, verb)(timeout=30)

    assert sandbox.state == target
    reads = api.reads()[len(before) :]
    assert len(reads) == 2
    assert reads[0].params == {}
    assert reads[1].params.get("wait") == frame


@pytest.mark.parametrize(("verb", "state", "before", "frame", "stale", "target"), _PRE_OPERATION)
async def test_a_held_state_before_the_operation_is_decided_by_long_polls(
    verb: str, state: str, before: list[object], frame: str, stale: str, target: str
) -> None:
    # The hub still holds the state from before the operation, and the read
    # that confirms it lags too.
    api = _Api([*before, stale, frame])
    sandbox = _sandbox(api, _ScriptedHub(stale), state=state)

    await getattr(sandbox, verb)(timeout=30)

    assert sandbox.state == target
    assert api.reads()[-1].params.get("wait") == frame


@pytest.mark.parametrize(("verb", "state", "before", "frame", "stale", "target"), _PRE_OPERATION)
async def test_an_operation_that_failed_is_reported_by_the_long_poll(
    verb: str, state: str, before: list[object], frame: str, stale: str, target: str
) -> None:
    del target
    api = _Api([*before, stale])
    sandbox = _sandbox(api, _ScriptedHub(frame), state=state)

    with pytest.raises(SandboxTerminalStateError) as raised:
        await getattr(sandbox, verb)(timeout=30)

    assert raised.value.raw_state == stale
    assert api.reads()[-1].params.get("wait") == frame


async def test_a_read_showing_an_irreversible_state_after_a_target_event_raises_at_once() -> None:
    api = _Api(["deleting"])
    sandbox = _sandbox(api, _ScriptedHub("paused"), state="started")

    with pytest.raises(SandboxTerminalStateError) as raised:
        await sandbox.pause(timeout=30)

    assert (raised.value.state, raised.value.raw_state) == ("destroying", "deleting")
    assert len(api.reads()) == 1


@pytest.mark.parametrize(("verb", "state", "before", "frame", "stale", "target"), _PRE_OPERATION)
async def test_a_held_failure_state_that_every_read_confirms_is_reported_by_the_long_poll(
    verb: str, state: str, before: list[object], frame: str, stale: str, target: str
) -> None:
    # The operation failed: the hub reports the state before it (for a pause,
    # `started`), and every read agrees. The confirming plain read does not
    # decide it; the long poll for the target does.
    del target
    api = _Api([*before, stale])
    sandbox = _sandbox(api, _ScriptedHub(stale), state=state)

    with pytest.raises(SandboxTerminalStateError) as raised:
        await getattr(sandbox, verb)(timeout=30)

    assert raised.value.raw_state == stale
    reads = api.reads()[len(before) :]
    assert [read.params.get("wait") for read in reads] == [None, frame]
