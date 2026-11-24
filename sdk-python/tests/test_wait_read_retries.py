# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Retries of a wait's failed observation reads.

Sandbox state waits, Docker readiness and image / snapshot readiness retry a
read that fails transiently (no response, a stalled read, HTTP 429 or a 5xx
other than 501) with back-off under the wait's own deadline, honouring the
server's retry hint; a spent deadline raises the wait's timeout with the last
failure as its cause, and any other failure is raised at once. The TypeScript
twin is ``tests/wait-read-retries.test.ts``; Go's is
``sandbox_wait_retry_test.go`` / ``read_retry_test.go``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Optional

import httpx
import pytest
from _handles import lazy_sandbox
from test_core import _sandbox_detail_payload, bind_api

from daytona.v2._core import RequestSpec, error_from_response
from daytona.v2._events import WaitOutcome, _run_poll_loop, _TargetPredicate
from daytona.v2._image_pull import ImagePull
from daytona.v2._read_retry import (
    READ_RETRY_MIN_DELAY_S,
    ReadRetry,
    retry_after_seconds,
    retryable_read_error,
)
from daytona.v2._transport import AsyncTransport
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.docker import AsyncDockerService
from daytona.v2.errors import (
    AuthenticationError,
    BadRequestError,
    ClientTimeoutError,
    ConnectionError,
    DaytonaError,
    DockerNotReadyError,
    DockerWaitTimeoutError,
    ForbiddenError,
    NotFoundError,
    RequestTimeoutError,
    SandboxTerminalStateError,
    ServiceUnavailableError,
    create_error,
)
from daytona.v2.image import ImageBuilder
from daytona.v2.services import AsyncImagesService, AsyncSnapshotsService
from daytona.v2.types import DaytonaConfig

pytestmark = pytest.mark.anyio

_TRANSIENT = [
    pytest.param(lambda: create_error("bad gateway", 502, None, None), id="502"),
    pytest.param(lambda: create_error("busy", 503, "SERVICE_UNAVAILABLE", "DAYTONA_API"), id="503"),
    pytest.param(lambda: create_error("gateway timeout", 504, None, None), id="504"),
    pytest.param(lambda: create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"), id="500"),
    pytest.param(lambda: create_error("slow down", 429, "RATE_LIMITED", "DAYTONA_API"), id="429"),
    pytest.param(lambda: ConnectionError("connection reset", source="SDK"), id="connection"),
    pytest.param(ClientTimeoutError, id="client-timeout"),
]

_PERMANENT = [
    pytest.param(lambda: create_error("bad", 400, "BAD_REQUEST", "DAYTONA_API"), BadRequestError, id="400"),
    pytest.param(
        lambda: create_error("no", 401, "UNAUTHORIZED", "DAYTONA_API"), AuthenticationError, id="401"
    ),
    pytest.param(lambda: create_error("no", 403, "FORBIDDEN", "DAYTONA_API"), ForbiddenError, id="403"),
    pytest.param(lambda: create_error("gone", 404, "NOT_FOUND", "DAYTONA_API"), NotFoundError, id="404"),
    pytest.param(
        lambda: create_error("server 408", 408, "REQUEST_TIMEOUT", "DAYTONA_API"),
        RequestTimeoutError,
        id="408",
    ),
    pytest.param(lambda: create_error("nope", 501, None, None), DaytonaError, id="501"),
    pytest.param(
        lambda: create_error("limit", 429, "LIMIT_EXCEEDED", "DAYTONA_API"), DaytonaError, id="limit"
    ),
]


class _Scripted:
    """A transport that answers from a script; its last answer repeats."""

    request_timeout_seconds = 30.0
    max_response_bytes = 0

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []
        self.times: list[float] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        self.times.append(time.monotonic())
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self) -> None:
        return None


def _gaps(transport: _Scripted) -> list[float]:
    return [later - earlier for earlier, later in zip(transport.times, transport.times[1:])]


def _sandbox(transport: _Scripted):  # type: ignore[no-untyped-def]
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    bind_api(client, transport)  # type: ignore[arg-type]
    return lazy_sandbox(client, "sbx")


# --- the policy ---------------------------------------------------------------


@pytest.mark.parametrize("make", [p.values[0] for p in _TRANSIENT], ids=[p.id for p in _TRANSIENT])
def test_transient_read_failures_are_retryable(make) -> None:  # type: ignore[no-untyped-def]
    assert retryable_read_error(make())


@pytest.mark.parametrize("make", [p.values[0] for p in _PERMANENT], ids=[p.id for p in _PERMANENT])
def test_permanent_read_failures_are_not_retryable(make) -> None:  # type: ignore[no-untyped-def]
    assert not retryable_read_error(make())


def test_the_wait_timeout_and_other_errors_are_not_retryable() -> None:
    wait_timeout = create_error("wait", 408, "REQUEST_TIMEOUT", "SDK")
    assert not retryable_read_error(wait_timeout)
    assert not retryable_read_error(ValueError("not an SDK error"))


async def test_the_back_off_doubles_to_five_seconds_and_resets_on_success() -> None:
    retry = ReadRetry()
    failure = create_error("busy", 503, None, None)
    delays = []
    for _ in range(7):
        assert retry.note(failure)
        delays.append(round(retry._delay, 3))
    assert delays == [0.2, 0.4, 0.8, 1.6, 3.2, 5.0, 5.0]
    retry.succeeded()
    assert retry.note(failure) and retry._delay == READ_RETRY_MIN_DELAY_S
    # A success keeps the last failure as the timeout cause.
    assert retry.last_error is failure


def test_the_retry_hint_prefers_the_body_then_the_header() -> None:
    error = error_from_response(
        503,
        b'{"code":"OVERLOADED","retryAfterMs":1500,"retry_after_seconds":4}',
        content_type="application/json",
        retry_after="9",
    )
    assert retry_after_seconds(error) == 1.5
    error = error_from_response(
        429,
        b'{"code":"RATE_LIMITED","retry_after_seconds":4}',
        content_type="application/json",
        retry_after="9",
    )
    assert retry_after_seconds(error) == 4
    error = error_from_response(503, b"<html>busy</html>", content_type="text/html", retry_after="9")
    assert retry_after_seconds(error) == 9
    # The public fields carry only what the body says.
    assert error.retry_after_seconds is None and error.retry_after_ms is None
    later = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    error = error_from_response(503, b"", content_type=None, retry_after=later)
    assert 25 <= retry_after_seconds(error) <= 30
    for unusable in ("soon", "-1", "", None, "9" * 400, "9" * 5000, "\u0665"):
        error = error_from_response(503, b"", content_type=None, retry_after=unusable)
        assert retry_after_seconds(error) == 0


async def test_the_transport_keeps_the_retry_after_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "2"}, text="busy")

    api = AsyncTransport(
        "https://api.example", "key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ServiceUnavailableError) as raised:
        await api.send(RequestSpec("GET", "/sandboxes/sbx"))
    assert retry_after_seconds(raised.value) == 2


# --- sandbox waits --------------------------------------------------------------


@pytest.mark.parametrize("make", [p.values[0] for p in _TRANSIENT], ids=[p.id for p in _TRANSIENT])
async def test_a_polling_wait_retries_one_transient_read_failure(make) -> None:  # type: ignore[no-untyped-def]
    transport = _Scripted([make(), _sandbox_detail_payload("sbx", "running")])
    sandbox = _sandbox(transport)
    await sandbox.wait(until="started", timeout=20)
    assert sandbox.state == "started"
    assert len(transport.specs) == 2
    assert _gaps(transport)[0] >= READ_RETRY_MIN_DELAY_S - 0.01


@pytest.mark.parametrize(("make", "raised"), [p.values for p in _PERMANENT], ids=[p.id for p in _PERMANENT])
async def test_a_polling_wait_raises_a_permanent_read_failure_at_once(make, raised) -> None:  # type: ignore[no-untyped-def]
    failure = make()
    transport = _Scripted([failure, _sandbox_detail_payload("sbx", "running")])
    with pytest.raises(raised) as caught:
        await _sandbox(transport).wait(until="started", timeout=20)
    assert caught.value is failure
    assert len(transport.specs) == 1


async def test_a_polling_wait_keeps_the_terminal_answer_after_a_retry() -> None:
    transport = _Scripted(
        [create_error("busy", 503, None, None), {**_sandbox_detail_payload("sbx", "failed"), "error": "boom"}]
    )
    with pytest.raises(SandboxTerminalStateError) as raised:
        await _sandbox(transport).wait(until="started", timeout=20)
    assert (raised.value.raw_state, raised.value.reason) == ("failed", "boom")
    assert len(transport.specs) == 2


async def test_a_polling_wait_honours_the_servers_retry_hint() -> None:
    hinted = create_error("slow down", 429, "RATE_LIMITED", "DAYTONA_API", retry_after_ms=700)
    transport = _Scripted([hinted, _sandbox_detail_payload("sbx", "running")])
    await _sandbox(transport).wait(until="started", timeout=20)
    assert _gaps(transport)[0] >= 0.69


async def test_a_retry_hint_past_the_deadline_ends_the_wait_at_its_deadline() -> None:
    hinted = create_error("slow down", 429, "RATE_LIMITED", "DAYTONA_API", retry_after_seconds=60)
    transport = _Scripted([hinted])
    started = time.monotonic()
    with pytest.raises(RequestTimeoutError) as raised:
        await _sandbox(transport).wait(until="started", timeout=1)
    assert 0.9 <= time.monotonic() - started < 2
    assert raised.value.source == "SDK"
    assert raised.value.__cause__ is hinted
    assert len(transport.specs) == 1


async def test_a_read_that_keeps_failing_ends_in_the_wait_timeout_with_its_cause() -> None:
    failure = create_error("busy", 503, "SERVICE_UNAVAILABLE", "DAYTONA_API")
    transport = _Scripted([failure])
    with pytest.raises(RequestTimeoutError) as raised:
        await _sandbox(transport).wait(until="started", timeout=1)
    assert (raised.value.source, raised.value.code) == ("SDK", "REQUEST_TIMEOUT")
    assert raised.value.__cause__ is failure
    # 0.2 + 0.4 s of back-off, then a read cut by the deadline (fewer on a slow host).
    assert 2 <= len(transport.specs) <= 4


async def test_a_success_between_failures_keeps_the_last_failure_as_the_cause() -> None:
    failure = ConnectionError("reset", source="SDK")
    transport = _Scripted([failure, _sandbox_detail_payload("sbx", "booting")])
    with pytest.raises(RequestTimeoutError) as raised:
        await _sandbox(transport).wait(until="started", timeout=0.6)
    assert raised.value.__cause__ is failure


async def test_the_events_fallback_poll_loop_retries_a_transient_failure() -> None:
    # A hub in fallback mode polls through _run_poll_loop; the sandbox's poll
    # function records each failure in the shared retry and waits it out.
    failure = create_error("bad gateway", 502, None, None)
    predicate = _TargetPredicate(states=frozenset({"started"}), terminal_states=frozenset({"error"}))

    def polling(retry: ReadRetry, script: list[object]):  # type: ignore[no-untyped-def]
        async def poll() -> WaitOutcome:
            await retry.pause(None)
            step = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(step, DaytonaError):
                retry.probe_failed(step)
                raise step
            retry.succeeded()
            return WaitOutcome("started", "running")

        return poll

    retry = ReadRetry()
    started = time.monotonic()
    outcome = await _run_poll_loop(
        "sbx", predicate, deadline=started + 5, fn=polling(retry, [failure, "ok"]), retry=retry
    )
    assert outcome.state == "started"
    assert time.monotonic() - started >= READ_RETRY_MIN_DELAY_S - 0.01

    retry = ReadRetry()
    with pytest.raises(RequestTimeoutError) as raised:
        await _run_poll_loop(
            "sbx", predicate, deadline=time.monotonic() + 0.3, fn=polling(retry, [failure]), retry=retry
        )
    assert raised.value.__cause__ is failure
    refused = create_error("no", 401, "UNAUTHORIZED", "DAYTONA_API")
    retry = ReadRetry()
    with pytest.raises(AuthenticationError):
        await _run_poll_loop(
            "sbx", predicate, deadline=time.monotonic() + 5, fn=polling(retry, [refused]), retry=retry
        )


# --- Docker readiness -------------------------------------------------------------

_STARTING = {"state": "starting", "owner": "daemon"}
_READY = {"state": "ready", "owner": "daemon"}


@pytest.mark.parametrize("make", [p.values[0] for p in _TRANSIENT], ids=[p.id for p in _TRANSIENT])
async def test_docker_readiness_retries_a_transient_status_read(make) -> None:  # type: ignore[no-untyped-def]
    transport = _Scripted([_STARTING, make(), _READY])
    status = await AsyncDockerService(transport).wait_until_ready(timeout=20)  # type: ignore[arg-type]
    assert status.state == "ready"
    assert len(transport.specs) == 3


async def test_docker_readiness_raises_a_permanent_failure_and_keeps_terminal_states() -> None:
    missing = create_error("no docker status", 404, "NOT_FOUND", "DAYTONA_DAEMON")
    with pytest.raises(NotFoundError) as raised:
        await AsyncDockerService(_Scripted([missing])).wait_until_ready(timeout=20)  # type: ignore[arg-type]
    assert raised.value is missing
    transport = _Scripted([create_error("busy", 503, None, None), {"state": "failed", "owner": "daemon"}])
    with pytest.raises(DockerNotReadyError):
        await AsyncDockerService(transport).wait_until_ready(timeout=20)  # type: ignore[arg-type]


async def test_docker_readiness_that_keeps_failing_is_the_docker_timeout_with_its_cause() -> None:
    failure = create_error("busy", 503, None, None)
    transport = _Scripted([_STARTING, failure])
    with pytest.raises(DockerWaitTimeoutError) as raised:
        await AsyncDockerService(transport).wait_until_ready(timeout=1)  # type: ignore[arg-type]
    assert raised.value.status is not None and raised.value.status.state == "starting"
    assert raised.value.__cause__ is failure


# --- image and snapshot readiness ------------------------------------------------


def _artifact(status: str) -> dict[str, object]:
    return {
        "id": "img",
        "name": None,
        "status": status,
        "region_id": "region-a",
        "anonymous": True,
        "retention": "idle",
        "origin": "dockerfile",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "format_version": 2,
        "format_outdated": False,
        "platform": False,
        "default": False,
        "deprecated": False,
        "class": "linux",
        "region_ids": ["region-a"],
    }


async def test_artifact_readiness_retries_an_intermediate_client_timeout() -> None:
    # A stalled hold with most of a 600 s budget left is retried, not raised.
    transport = _Scripted([ClientTimeoutError(), _artifact("ready")])
    image = await AsyncImagesService(transport).get("img", wait="ready", timeout=600)  # type: ignore[arg-type]
    assert image.status == "ready"
    assert len(transport.specs) == 2


@pytest.mark.parametrize("make", [p.values[0] for p in _TRANSIENT], ids=[p.id for p in _TRANSIENT])
async def test_artifact_readiness_retries_a_transient_read(make) -> None:  # type: ignore[no-untyped-def]
    transport = _Scripted([_artifact("building"), make(), _artifact("ready")])
    image = await AsyncImagesService(transport).get("img", wait="ready", timeout=600)  # type: ignore[arg-type]
    assert image.status == "ready"
    assert len(transport.specs) == 3


async def test_snapshot_readiness_retries_a_transient_read_and_returns_a_failure() -> None:
    snapshot = {
        "id": "snap",
        "cursor": "cursor",
        "class": "linux",
        "kind": "full",
        "name": "snap",
        "region_id": "us",
        "status": "failed",
        "region_ids": ["us"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    transport = _Scripted([create_error("busy", 503, None, None), snapshot])
    result = await AsyncSnapshotsService(transport).get("snap", wait="ready", timeout=60)  # type: ignore[arg-type]
    assert result.status == "failed"
    assert len(transport.specs) == 2


async def test_artifact_readiness_raises_a_permanent_failure_at_once() -> None:
    missing = create_error("no such image", 404, "NOT_FOUND", "DAYTONA_API")
    transport = _Scripted([missing])
    with pytest.raises(NotFoundError) as raised:
        await AsyncImagesService(transport).get("img", wait="ready", timeout=60)  # type: ignore[arg-type]
    assert raised.value is missing
    assert len(transport.specs) == 1


async def test_artifact_readiness_that_keeps_failing_times_out_with_its_cause() -> None:
    failure = create_error("busy", 503, None, None)
    transport = _Scripted([failure])
    with pytest.raises(RequestTimeoutError) as raised:
        await AsyncImagesService(transport).get("img", wait="ready", timeout=1)  # type: ignore[arg-type]
    assert (raised.value.source, str(raised.value)) == ("SDK", "artifact did not become ready before timeout")
    assert raised.value.__cause__ is failure


async def test_image_pull_retries_a_transient_readiness_read() -> None:
    transport = _Scripted(
        [
            {"id": "img", "status": "pending"},
            _artifact("pending"),
            create_error("bad gateway", 502, None, None),
            _artifact("ready"),
        ]
    )
    image = await ImagePull(transport, 60).run(RequestSpec("POST", "/images"), wait=True)  # type: ignore[arg-type]
    assert image.status == "ready"
    assert [spec.method for spec in transport.specs] == ["POST", "GET", "GET", "GET"]


async def test_image_pull_that_keeps_failing_times_out_with_its_cause() -> None:
    failure = create_error("busy", 503, None, None)
    transport = _Scripted([{"id": "img", "status": "pending"}, _artifact("pending"), failure])
    with pytest.raises(RequestTimeoutError) as raised:
        await ImagePull(transport, 1.5).run(RequestSpec("POST", "/images"), wait=True)  # type: ignore[arg-type]
    assert str(raised.value) == "image pull did not finish before timeout"
    assert raised.value.__cause__ is failure


# --- review follow-ups ------------------------------------------------------------


@pytest.mark.parametrize("field", ["retry_after_seconds", "retryAfterMs"])
async def test_a_body_hint_too_large_for_a_float_is_no_hint(field: str) -> None:
    huge = "9" * 400
    error = error_from_response(
        429,
        f'{{"code":"RATE_LIMITED","message":"slow down","{field}":{huge}}}'.encode(),
        content_type="application/json",
        retry_after="1",
    )
    # The body's hint is unusable: the header's is used, and nothing overflows.
    assert retry_after_seconds(error) == 1
    assert ReadRetry().note(error)
    transport = _Scripted([error, _artifact("ready")])
    image = await AsyncImagesService(transport).get("img", wait="ready", timeout=5)  # type: ignore[arg-type]
    assert image.status == "ready"


def test_a_negative_body_hint_is_no_hint() -> None:
    error = create_error("busy", 503, None, None, retry_after_seconds=-5, retry_after_ms=-1)
    assert retry_after_seconds(error) == 0


async def test_docker_readiness_keeps_its_poll_spacing_after_a_failure() -> None:
    transport = _Scripted([create_error("slow down", 429, "RATE_LIMITED", "DAYTONA_DAEMON"), _READY])
    await AsyncDockerService(transport).wait_until_ready(timeout=20)  # type: ignore[arg-type]
    assert _gaps(transport)[0] >= 0.49


async def test_image_pull_retries_a_transient_first_lookup() -> None:
    transport = _Scripted(
        [{"id": "img", "status": "pending"}, ConnectionError("reset", source="SDK"), _artifact("ready")]
    )
    image = await ImagePull(transport, 60).run(RequestSpec("POST", "/images"), wait=True)  # type: ignore[arg-type]
    assert image.status == "ready"
    assert [spec.method for spec in transport.specs] == ["POST", "GET", "GET"]
    # Without wait the pull returns its first observation and retries nothing.
    failure = ConnectionError("reset", source="SDK")
    transport = _Scripted([{"id": "img", "status": "pending"}, failure, _artifact("ready")])
    with pytest.raises(ConnectionError) as raised:
        await ImagePull(transport, 60).run(RequestSpec("POST", "/images"), wait=False)  # type: ignore[arg-type]
    assert raised.value is failure


async def test_an_artifact_wait_cut_after_an_unsettled_read_keeps_the_last_failure() -> None:
    failure = create_error("busy", 503, None, None)

    class _Slow(_Scripted):
        async def send(self, spec: RequestSpec) -> object:
            if len(self.specs) == 1:
                await asyncio.sleep(0.6)
            return await super().send(spec)

    transport = _Slow([failure, _artifact("building")])
    with pytest.raises(RequestTimeoutError) as raised:
        await AsyncImagesService(transport).get("img", wait="ready", timeout=0.5)  # type: ignore[arg-type]
    assert raised.value.__cause__ is failure


@pytest.mark.parametrize("build_timeout", [None, 0, 60])
async def test_create_from_build_retries_a_transient_readiness_read(build_timeout: Optional[float]) -> None:
    transport = _Scripted(
        [
            {"id": "img", "status": "pending"},
            _artifact("pending"),
            create_error("bad gateway", 502, None, None),
            _artifact("ready"),
            {**_sandbox_detail_payload("sbx", "running"), "wait_cap_seconds": 0},
        ]
    )
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    bind_api(client, transport)  # type: ignore[arg-type]
    sandbox = await client.create_from_build(
        ImageBuilder.from_registry("python:3.12").run_commands("true"),
        build_timeout=build_timeout,
        wait_seconds=0,
    )
    assert sandbox.id == "sbx"
    assert [spec.method for spec in transport.specs] == ["POST", "GET", "GET", "GET", "POST"]
    retried = transport.specs[3]
    if not build_timeout:
        # The default budget repeats full holds with no deadline (the read gets
        # the hold plus its 5 s slack), never the single unbounded hold.
        assert (retried.params["timeout_ms"], retried.timeout) == (60_000, 65.0)
    else:
        assert retried.timeout is not None and retried.timeout <= 60


class _ProbingHub:
    """A hub whose early probe fails once, then falls back to polling."""

    enabled = True

    def __init__(self, then_time_out: bool = False) -> None:
        self.then_time_out = then_time_out

    async def wait(self, sandbox_id: str, predicate: object, **kwargs: object) -> WaitOutcome:
        poll = kwargs["sandbox_poll_fn"]
        assert callable(poll)
        with contextlib.suppress(DaytonaError):
            await poll()
        if self.then_time_out:
            raise RequestTimeoutError(
                "hub share spent", status_code=408, code="REQUEST_TIMEOUT", source="SDK"
            )
        return await poll()  # type: ignore[no-any-return]


async def test_a_failed_probe_holds_back_the_fallback_read_by_its_retry_hint() -> None:
    hinted = create_error("slow down", 429, "RATE_LIMITED", "DAYTONA_API", retry_after_ms=600)
    transport = _Scripted([hinted, _sandbox_detail_payload("sbx", "running")])
    sandbox = _sandbox(transport)
    sandbox._hub = _ProbingHub()  # type: ignore[assignment]
    await sandbox.wait(until="started", timeout=20)
    assert sandbox.state == "started"
    assert _gaps(transport)[0] >= 0.59


async def test_a_failed_probe_that_is_not_retried_is_still_the_timeouts_cause() -> None:
    # The hub keeps waiting through a probe's 400 (it stops only on 401, 403
    # and 404); the wait's timeout then carries it.
    refused = create_error("bad", 400, "BAD_REQUEST", "DAYTONA_API")
    transport = _Scripted([refused, _sandbox_detail_payload("sbx", "booting")])
    sandbox = _sandbox(transport)
    sandbox._hub = _ProbingHub(then_time_out=True)  # type: ignore[assignment]
    with pytest.raises(RequestTimeoutError) as raised:
        await sandbox.wait(until="started", timeout=1)
    assert raised.value.source == "SDK"
    assert raised.value.__cause__ is refused
