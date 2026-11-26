# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Transition retries retain the last error and do not turn into general retries."""

from __future__ import annotations

import anyio
import httpx
import pytest
import respx

from daytona.v2 import ClientClosedError, ClientTimeoutError, ConflictError, _clock, _transition_retry
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import create_error

_SPEC = RequestSpec("POST", "/sandboxes/src/fork", json_body={})
_CODE = "SANDBOX_TRANSITION_IN_PROGRESS"


class Clock:
    """Mutable deterministic retry clock; real scheduling stays untouched."""

    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(_clock, "monotonic", clock.monotonic)
    monkeypatch.setattr(_transition_retry, "sleep", clock.sleep)
    return clock


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(("hint", "delay"), [(None, 1), (0, 1), (1, 1), (3, 3), (90, 5)])
async def test_retry_delay_is_clamped(clock: Clock, hint: int | None, delay: int) -> None:
    # Given a busy sandbox followed by a successful submission.
    payload: dict[str, str | int] = {"code": _CODE, "source": "DAYTONA_API", "message": "busy"}
    if hint is not None:
        payload["retry_after_seconds"] = hint
    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(
        side_effect=[httpx.Response(409, json=payload), httpx.Response(202, json={"id": "fork"})]
    )
    api = AsyncTransport("https://api.invalid", "key")
    try:
        # When submitting the operation.
        result = await _transition_retry.send_with_transition_retry(api, _SPEC)
    finally:
        await api.close()
    # Then only the bounded hint controls the retry.
    assert result == {"id": "fork"}
    assert clock.delays == [delay]
    assert route.call_count == 2


@pytest.mark.anyio
@respx.mock
async def test_expiry_raises_the_same_last_error(clock: Clock) -> None:
    # Given distinct transition errors for each attempt.
    errors = [ConflictError(f"busy-{index}", code=_CODE, retry_after_seconds=5) for index in range(6)]
    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(side_effect=errors)
    api = AsyncTransport("https://api.invalid", "key")
    try:
        # When the transition remains busy for the whole budget.
        with pytest.raises(ConflictError) as raised:
            await _transition_retry.send_with_transition_retry(api, _SPEC)
    finally:
        await api.close()
    # Then the last server error, not a manufactured timeout, is raised unchanged.
    assert raised.value is errors[-1]
    assert clock.now == 30
    assert route.call_count == 6


@pytest.mark.anyio
@respx.mock
async def test_budget_starts_before_the_first_attempt(clock: Clock) -> None:
    # Given a first attempt that itself consumes most of the retry budget.
    error = ConflictError("busy", code=_CODE, retry_after_seconds=5)

    def first(request: httpx.Request) -> httpx.Response:
        clock.now = 29
        raise error

    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(side_effect=first)
    api = AsyncTransport("https://api.invalid", "key")
    try:
        # When there is no time for the next attempt.
        with pytest.raises(ConflictError) as raised:
            await _transition_retry.send_with_transition_retry(api, _SPEC)
    finally:
        await api.close()
    # Then the first attempt's time counts and the final sleep cannot overshoot.
    assert raised.value is error
    assert clock.delays == [1]
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("code", ["CONFLICT", "DISK_NOT_READY", "NETWORK_POLICY_TRANSITION_IN_PROGRESS"])
async def test_other_conflicts_are_not_retried(clock: Clock, code: str) -> None:
    # Given a conflict that does not promise a retryable sandbox transition.
    error = create_error("refused", 409, code, "DAYTONA_API")
    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(side_effect=error)
    api = AsyncTransport("https://api.invalid", "key")
    try:
        # When attempting submission.
        with pytest.raises(ConflictError) as raised:
            await _transition_retry.send_with_transition_retry(api, _SPEC)
    finally:
        await api.close()
    # Then the exact error is immediately visible to the caller.
    assert raised.value is error
    assert route.call_count == 1
    assert clock.delays == []


@pytest.mark.anyio
@respx.mock
async def test_close_during_backoff_refuses_a_new_request(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a client that closes during the backoff.
    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(
        return_value=httpx.Response(409, json={"code": _CODE, "message": "busy", "source": "DAYTONA_API"})
    )
    api = AsyncTransport("https://api.invalid", "key")

    async def close(seconds: float) -> None:
        await api.close()
        await clock.sleep(seconds)

    monkeypatch.setattr(_transition_retry, "sleep", close)
    # When the next attempt would begin.
    with pytest.raises(ClientClosedError):
        await _transition_retry.send_with_transition_retry(api, _SPEC)
    # Then no request escapes the closed client.
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_cancellation_interrupts_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a transition conflict and a cancellable sleep.
    started = anyio.Event()
    finished = anyio.Event()
    cancelled = anyio.Event()

    async def sleeping(seconds: float) -> None:
        started.set()
        try:
            await anyio.sleep_forever()
        finally:
            finished.set()

    monkeypatch.setattr(_transition_retry, "sleep", sleeping)
    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(
        return_value=httpx.Response(409, json={"code": _CODE, "message": "busy", "source": "DAYTONA_API"})
    )
    api = AsyncTransport("https://api.invalid", "key")

    async def submit() -> None:
        try:
            await _transition_retry.send_with_transition_retry(api, _SPEC)
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    try:
        # When the caller cancels during backoff.
        async with anyio.create_task_group() as group:
            group.start_soon(submit)
            await started.wait()
            group.cancel_scope.cancel()
    finally:
        await api.close()
    # Then cancellation unwinds without another request.
    assert finished.is_set()
    assert cancelled.is_set()
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_request_timeout_is_not_retried(clock: Clock) -> None:
    # Given an ordinary transport deadline error after one transition response.
    error = ClientTimeoutError()
    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(
        side_effect=[ConflictError("busy", code=_CODE), error]
    )
    api = AsyncTransport("https://api.invalid", "key")
    try:
        # When the retry hits its request deadline.
        with pytest.raises(ClientTimeoutError) as raised:
            await _transition_retry.send_with_transition_retry(api, _SPEC)
    finally:
        await api.close()
    # Then the original transport failure ends the operation.
    assert raised.value is error
    assert route.call_count == 2


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("accepted", [True, False])
async def test_admitted_attempt_keeps_its_request_deadline(clock: Clock, accepted: bool) -> None:
    # Given a retry admitted at second 29 that finishes after the admission window.
    attempts = 0
    refusal = ConflictError("still busy", code=_CODE, retry_after_seconds=5)

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            clock.now = 24
            raise ConflictError("busy", code=_CODE, retry_after_seconds=5)
        assert clock.now == 29
        # The ordinary 60-second request deadline must not become one second.
        assert anyio.current_effective_deadline() - anyio.current_time() > 30
        clock.now = 31
        await anyio.lowlevel.checkpoint()
        if accepted:
            return httpx.Response(202, json={"id": "fork"})
        raise refusal

    route = respx.post("https://api.invalid/sandboxes/src/fork").mock(side_effect=respond)
    api = AsyncTransport("https://api.invalid", "key", request_timeout=60)
    try:
        # When the admitted request completes after the retry window.
        if accepted:
            result = await _transition_retry.send_with_transition_retry(api, _SPEC)
            assert result == {"id": "fork"}
        else:
            with pytest.raises(ConflictError) as raised:
                await _transition_retry.send_with_transition_retry(api, _SPEC)
            assert raised.value is refusal
    finally:
        await api.close()
    # Then acceptance survives; a refusal cannot admit a third request.
    assert route.call_count == 2
