# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Process waits are a series of bounded rounds, never one request held for
the whole caller budget. The TypeScript suite's `process wait rounds` block
(tests/process-wait-rounds.test.ts) holds the same expectations."""

from __future__ import annotations

from typing import Optional

import pytest

from daytona.v2._core import PROCESS_WAIT_ROUND_SECONDS, WAIT_READ_SLACK_SECONDS, RequestSpec
from daytona.v2.errors import ClientTimeoutError, ConnectionError
from daytona.v2.process import AsyncProcessService
from daytona.v2.session import AsyncSession

_ROUND = PROCESS_WAIT_ROUND_SECONDS


def _process(state: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "proc",
        "kind": "exec",
        "state": state,
        "createdAt": "2026-08-24T00:00:00Z",
        "cwd": "/workspace",
        "stdin": "closed",
        "keepLogs": "on_exit_ttl",
        "truncatedHead": False,
    }
    payload.update(overrides)
    return payload


def _eof_page(data: str = "") -> dict[str, object]:
    frames = [] if not data else [{"cursor": "1", "channel": "stdout", "data": data, "timestamp": 0}]
    return {"frames": frames, "nextCursor": "1", "eof": True, "truncatedHead": False}


class HoldingTransport:
    """Answers each request in order and advances a fake clock by the time
    the server would hold it: a wait round its full ``timeoutMs`` / ``waitMs``
    hold when it answers ``timed_out`` / a running record."""

    request_timeout_seconds = 60.0
    max_response_bytes = 0

    def __init__(self, responses: list[object], clock: list[float]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []
        self.clock = clock

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            # A dropped or stalled request fails at its own read bound.
            self.clock[0] += spec.timeout or 0
            raise response
        params = spec.params or {}
        held = isinstance(response, dict) and (
            response.get("reason") == "timed_out" or response.get("state") == "running"
        )
        hold_ms = params.get("timeoutMs", params.get("waitMs"))
        if held and isinstance(hold_ms, int):
            self.clock[0] += hold_ms / 1000
        return response

    async def close(self) -> None:
        pass


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [0.0]
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: now[0])
    return now


def _waits(transport: HoldingTransport) -> list[RequestSpec]:
    return [spec for spec in transport.specs if spec.path.endswith("/wait")]


def _assert_bounded_round(spec: RequestSpec, hold_ms: int) -> None:
    hold_key = "waitMs" if spec.method == "POST" else "timeoutMs"
    assert spec.params is not None
    assert spec.params[hold_key] == hold_ms
    assert spec.timeout == pytest.approx(hold_ms / 1000 + WAIT_READ_SLACK_SECONDS)
    assert not spec.unbounded_read


@pytest.mark.anyio
async def test_run_with_a_long_budget_waits_in_bounded_rounds(clock: list[float]) -> None:
    # run(timeout=100) on a command that runs ~70 s: the start request holds
    # one 30 s round, then /wait rounds of at most 30 s, never 100 s at once.
    transport = HoldingTransport(
        [
            _process("running"),
            {"reason": "timed_out"},
            _process("running"),
            {"reason": "exited", "exitCode": 0},
            _eof_page("done"),
        ],
        clock,
    )
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    result = await process.run("sleep 70; printf done", timeout=100)

    assert (result.reason, result.exit_code, result.stdout) == ("exited", 0, "done")
    _assert_bounded_round(transport.specs[0], 30000)
    waits = _waits(transport)
    assert len(waits) == 2
    for spec in waits:
        _assert_bounded_round(spec, 30000)


@pytest.mark.anyio
async def test_run_keeps_the_caller_deadline_across_rounds(clock: list[float]) -> None:
    # A 70 s budget: 30 s on the start request, a 30 s round, then a last
    # round of only the 10 s that are left; then the call times out.
    transport = HoldingTransport(
        [
            _process("running"),
            {"reason": "timed_out"},
            _process("running"),
            {"reason": "timed_out"},
            _process("running"),
            _eof_page(),
        ],
        clock,
    )
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    result = await process.run("sleep 600", timeout=70)

    assert result.reason == "timed_out"
    assert [spec.params["timeoutMs"] for spec in _waits(transport) if spec.params] == [30000, 10000]
    assert clock[0] == pytest.approx(70)


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [None, 0, 10**9])
async def test_an_unlimited_wait_is_a_series_of_bounded_rounds(
    clock: list[float], timeout: Optional[float]
) -> None:
    transport = HoldingTransport(
        [
            {"reason": "timed_out"},
            _process("running"),
            {"reason": "timed_out"},
            _process("running"),
            {"reason": "exited", "exitCode": 0},
        ],
        clock,
    )
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    result = await handle.wait(timeout=timeout)

    assert (result.reason, result.exit_code) == ("exited", 0)
    waits = _waits(transport)
    assert len(waits) == 3
    for spec in waits:
        _assert_bounded_round(spec, 30000)


@pytest.mark.anyio
async def test_a_round_dropped_by_the_network_is_retried_within_the_budget(clock: list[float]) -> None:
    # An intermediary drops one quiet round and another stalls past its read
    # bound: each costs one round, and the wait goes on to the exit.
    transport = HoldingTransport(
        [
            ConnectionError("connection reset"),
            ClientTimeoutError(),
            {"reason": "exited", "exitCode": 0},
        ],
        clock,
    )
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    result = await handle.wait()

    assert (result.reason, result.exit_code) == ("exited", 0)
    assert len(_waits(transport)) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [None, 600])
async def test_a_stalled_record_read_is_bounded_like_a_round_and_retried(
    clock: list[float], timeout: Optional[float]
) -> None:
    # After a `timed_out` round the wait reads the record; a dropped read
    # costs one round (35 s), never the rest of the budget, and the wait
    # goes on to the exit.
    transport = HoldingTransport(
        [{"reason": "timed_out"}, ClientTimeoutError(), {"reason": "exited", "exitCode": 0}],
        clock,
    )
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    result = await handle.wait(timeout=timeout)

    assert (result.reason, result.exit_code) == ("exited", 0)
    record_read = transport.specs[1]
    assert (record_read.method, record_read.path) == ("GET", "/processes/proc")
    assert record_read.timeout == pytest.approx(30 + WAIT_READ_SLACK_SECONDS)
    assert clock[0] == pytest.approx(30 + 35)


@pytest.mark.anyio
async def test_a_process_that_ends_with_the_last_round_is_reported(clock: list[float]) -> None:
    # The round that spends the budget answers `timed_out`; the record read
    # after it finds the process ended (here by its own execution deadline),
    # and that result is returned rather than the wait's `timed_out`.
    transport = HoldingTransport(
        [
            {"reason": "timed_out"},
            _process("terminal", reason="timed_out", exitCode=124, signal="SIGTERM"),
        ],
        clock,
    )
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    result = await handle.wait(timeout=10)

    assert (result.reason, result.exit_code, result.signal) == ("timed_out", 124, "TERM")
    assert transport.specs[1].timeout == pytest.approx(WAIT_READ_SLACK_SECONDS)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [ConnectionError("connection reset"), ClientTimeoutError()])
async def test_run_never_retries_the_start_request(clock: list[float], failure: Exception) -> None:
    # The process may have started before the start request failed, so a
    # retry could run the command twice: the failure is raised as is.
    transport = HoldingTransport([failure], clock)
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    with pytest.raises(type(failure)):
        await process.run("make deploy", timeout=600)

    assert [(spec.method, spec.path) for spec in transport.specs] == [("POST", "/processes")]
    _assert_bounded_round(transport.specs[0], 30000)


@pytest.mark.anyio
async def test_a_short_budget_holds_only_the_budget(clock: list[float]) -> None:
    transport = HoldingTransport([{"reason": "exited", "exitCode": 0}], clock)
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    await handle.wait(timeout=1.5)

    _assert_bounded_round(transport.specs[0], 1500)


@pytest.mark.anyio
async def test_a_session_run_bounds_its_start_request_the_same_way(clock: list[float]) -> None:
    terminal = _process("terminal", reason="exited", exitCode=0, logs=_eof_page("ok"))
    transport = HoldingTransport([terminal], clock)
    session = AsyncSession("sbx", "shell-1", transport)  # type: ignore[arg-type]

    result = await session.run("true", timeout=600)

    assert result.stdout == "ok"
    _assert_bounded_round(transport.specs[0], 30000)


def test_the_round_is_thirty_seconds() -> None:
    assert _ROUND == 30.0
