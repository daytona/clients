# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import anyio
import anyio.lowlevel
import httpx
import pytest
from _handles import lazy_sandbox

from daytona.v2 import (
    BadRequestError,
    BuildLogEvent,
    BuildLogGapEvent,
    BuildLogInterruptedError,
    BuildLogStateEvent,
    DaytonaError,
    NotFoundError,
)
from daytona.v2._core import RequestSpec
from daytona.v2._sync import Daytona
from daytona.v2._transport import AsyncTransport
from daytona.v2.build_logs import (
    _messages,
    _Observer,
    _ProtocolError,
    follow_build_logs,
    read_build_logs,
)
from daytona.v2.errors import _interrupt_error_of
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.services import AsyncImagesService
from daytona.v2.types import DaytonaConfig
from daytona.v2.types import _SandboxInfo as SandboxInfo

pytestmark = pytest.mark.anyio

UUID = "12345678-1234-1234-1234-123456789abc"
DISCOVERY = "/images/image/build-logs-url"
LOGS_URL = "https://logs.example/build-logs/images/image"


def cursor(checkpoint: int) -> str:
    return f"opaque:checkpoint/{checkpoint}+="


def event(name: str, data: object, ident: str | None = None) -> bytes:
    return (
        f"event: {name}\n" + (f"id: {ident}\n" if ident is not None else "") + f"data: {json.dumps(data)}\n\n"
    ).encode()


def log(raw: bytes, start: int) -> bytes:
    end = start + len(raw)
    return event(
        "log",
        {"data_base64": base64.b64encode(raw).decode(), "start_offset": str(start), "next_offset": str(end)},
        cursor(end),
    )


def done(offset: int | None = None, **changes: object) -> bytes:
    data: dict[str, object] = {"status": "ready", "reason": "terminal", "output_complete": True}
    if offset is not None:
        data["cursor"] = cursor(offset)
    data.update(changes)
    return event("done", data, cursor(offset) if offset is not None else None)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class Hanging(Chunks):
    """Delivers its chunks, then stays open without done until closed."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        await anyio.sleep(30)


def sse(stream: httpx.AsyncByteStream) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)


def setup(responses: list[bytes | httpx.Response], *, url: str = LOGS_URL):
    requests: list[httpx.Request] = []
    streams: list[Chunks] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("build-logs-url"):
            return httpx.Response(200, json={"url": url})
        if request.url.path == "/ordinary":
            return httpx.Response(200, json={"ok": True})
        response = responses.pop(0)
        if isinstance(response, httpx.Response):
            return response
        # Fragment every transport chunk at arbitrary byte boundaries.
        stream = Chunks([response[i : i + 7] for i in range(0, len(response), 7)])
        streams.append(stream)
        return sse(stream)

    # An injected client is borrowed: build-log streams run on it directly.
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    api = AsyncTransport("https://api.example", "secret-test-key", client=client)
    return api, requests, streams


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    async def no_sleep(_seconds: float) -> None:
        await anyio.lowlevel.checkpoint()

    monkeypatch.setattr("daytona.v2.build_logs._sleep", no_sleep)
    monkeypatch.setattr("daytona.v2.build_logs.random.uniform", lambda _a, _b: 1)


@contextmanager
def local_http_server(handler: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def local_stream_client() -> httpx.AsyncClient:
    # Only used against the plain-HTTP loopback fixture. Avoid loading a TLS CA
    # bundle inside the deliberately tiny 50 ms request budget.
    return httpx.AsyncClient(verify=False, trust_env=False)


def discovery_body(handler: BaseHTTPRequestHandler) -> bytes:
    return json.dumps({"url": f"http://{handler.headers['Host']}/build-logs/images/image"}).encode()


async def test_expired_server_state_still_delivers_explicit_deadline_completion():
    wire = event("state", {"status": "building", "deadline_at": "2000-01-01T00:00:00Z"})
    api, requests, _ = setup(
        [wire + log(b"tail", 0) + done(4, status="building", reason="deadline", output_complete=False)]
    )
    output: list[str] = []
    result = await follow_build_logs(api, DISCOVERY, on_logs=output.append, on_event=lambda _: None)
    assert output == ["tail"] and result.reason == "deadline" and result.cursor == cursor(4)
    assert not result.output_complete and len(requests) == 2


async def test_the_stream_media_type_is_read_case_insensitively():
    reply = httpx.Response(
        200, headers={"content-type": "Text/Event-Stream; charset=utf-8"}, content=log(b"tail", 0) + done(4)
    )
    api, _, _ = setup([reply])
    output: list[str] = []
    result = await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert output == ["tail"] and result.output_complete


async def test_awaitable_callbacks_are_awaited_before_the_next_event():
    api, _, _ = setup([log(b"a", 0) + log(b"b", 1) + done(2)])
    trace: list[str] = []

    async def on_logs(chunk: str) -> None:
        trace.append(f"start {chunk}")
        await anyio.sleep(0.01)
        trace.append(f"end {chunk}")

    async def on_event(item) -> None:
        trace.append(item.type)

    result = await follow_build_logs(api, DISCOVERY, on_logs=on_logs, on_event=on_event)
    assert result.cursor == cursor(2)
    assert trace == ["start a", "end a", "log", "start b", "end b", "log", "done"]


async def test_real_http_final_tail_arrives_after_server_deadline_and_request_timeout():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            if self.path.endswith("build-logs-url"):
                body = discovery_body(self)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            deadline = (datetime.now(timezone.utc) + timedelta(milliseconds=25)).isoformat()
            self.wfile.write(event("state", {"status": "building", "deadline_at": deadline}))
            self.wfile.flush()
            threading.Event().wait(0.15)
            self.wfile.write(
                log(b"final", 0) + done(5, status="building", reason="deadline", output_complete=False)
            )
            self.wfile.flush()
            self.close_connection = True

    with local_http_server(Handler) as base:
        api = AsyncTransport(
            base, "test-key", request_timeout=0.05, stream_client_factory=local_stream_client
        )
        output: list[str] = []
        try:
            result = await follow_build_logs(
                api, DISCOVERY, on_logs=output.append, on_event=lambda _: None, timeout=2
            )
            assert result.reason == "deadline" and result.cursor == cursor(5)
            assert output == ["final"] and len(requests) == 2
        finally:
            await api.close()


@pytest.mark.parametrize("stage,status", [("discovery", 200), ("target", 401), ("target", 403)])
async def test_real_http_finite_json_bodies_cannot_outlive_request_budget(stage, status):
    requests = []
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if stage == "target" and self.path.endswith("build-logs-url"):
                body = discovery_body(self)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(b'{"error":' if stage == "target" else b'{"url":')
                self.wfile.flush()
                # Dribble below the idle-read timeout, never completing JSON.
                # Only a whole-body budget can stop this before the watchdog.
                while not stop.wait(0.02):
                    self.wfile.write(b" ")
                    self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True

    with local_http_server(Handler) as base:
        api = AsyncTransport(
            base, "test-key", request_timeout=0.05, stream_client_factory=local_stream_client
        )
        try:
            with anyio.fail_after(3):
                with pytest.raises(BuildLogInterruptedError) as failure:
                    await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
            if stage == "target":
                assert failure.value.status_code == status
                # The denial is surfaced once, never retried: exactly one
                # target request, and it is the last. Discovery itself shares
                # the 50 ms budget, so on a loaded machine its first attempt can
                # time out and be retried before the target is reached
                # — legitimate, and bounded by the reconnect budget.
                *discoveries, target = requests
                assert target == "/build-logs/images/image?follow=true&format=sse", requests
                assert discoveries and set(discoveries) == {DISCOVERY}, requests
                assert len(discoveries) <= 6, requests
            else:
                assert "reconnect budget" in str(failure.value)
                # The client makes 6 discovery attempts (1 + 5 retries), each
                # with a 50 ms budget. `requests` is appended by the server
                # thread when do_GET starts, and on a loaded machine the sixth
                # attempt's budget can expire before the server dispatches it
                # at all, so waiting cannot guarantee it lands. Assert
                # what the budget does guarantee: only discovery was retried,
                # and never more than the 6 attempts the budget allows.
                for _ in range(50):
                    if len(requests) >= 6:
                        break
                    await anyio.sleep(0.01)
                assert set(requests) == {DISCOVERY}, requests
                assert 5 <= len(requests) <= 6, requests
        finally:
            stop.set()
            await api.close()


async def test_explicit_image_follow_preserves_utf8_across_reconnect_and_checkpoints():
    api, requests, streams = setup([log(b"\xf0\x9f", 0), log(b"\x98\x80!", 2) + done(5)])
    output: list[str] = []
    result = await AsyncImagesService(api).follow_build_logs("image", on_logs=output.append)
    assert output == ["😀!"]
    assert result.cursor == cursor(5) and result.output_complete
    assert [request.url.host for request in requests] == ["api.example", "logs.example", "logs.example"]
    assert requests[2].url.params["cursor"] == cursor(2)
    assert all(request.headers["authorization"] == "Bearer secret-test-key" for request in requests)
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("follow", [False, True])
async def test_sandbox_without_build_discovery_404_never_falls_back(follow):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"error": "sandbox has no image build", "code": "NOT_FOUND"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        api = AsyncTransport("https://api.example", "secret-test-key", client=client)
        sandbox = AsyncSandbox(SandboxInfo(id="registry-sbx"), api, None)
        output: list[str] = []
        if follow:
            with pytest.raises(BuildLogInterruptedError) as failure:
                await sandbox.follow_build_logs(on_logs=output.append, cursor=cursor(0))
            assert failure.value.cursor == cursor(0)
        else:
            with pytest.raises(NotFoundError) as failure:
                await sandbox.build_logs()
        assert failure.value.status_code == 404
        assert failure.value.code == "NOT_FOUND"
        assert output == []
        # Discovery errors are surfaced without guessing another URL.
        assert [str(request.url) for request in requests] == [
            "https://api.example/sandboxes/registry-sbx/build-logs-url",
        ]


@pytest.mark.parametrize(
    "sandbox_id,status,output",
    [
        ("failed-build", "failed", "Dockerfile build failed\n"),
        ("ready-build", "ready", "build output\n"),
    ],
)
@pytest.mark.parametrize("follow", [False, True])
async def test_sandbox_build_logs_use_discovered_build_without_cached_metadata(
    sandbox_id, status, output, follow
):
    target = f"https://logs.example/build-logs/sandboxes/{sandbox_id}"
    raw = output.encode()
    wire = log(raw, 0) + done(len(raw), status=status)
    text = f"state: {status}\n{output}"
    api, requests, _ = setup([wire if follow else httpx.Response(200, text=text)], url=target)
    # An unrefreshed handle needs only the sandbox ID for discovery.
    sandbox = AsyncSandbox(SandboxInfo(id=sandbox_id), api, None)
    try:
        if follow:
            received: list[str] = []
            result = await sandbox.follow_build_logs(on_logs=received.append)
            assert received == [output]
            assert result.status == status and result.output_complete
            assert result.cursor == cursor(len(raw))
        else:
            assert await sandbox.build_logs() == text
        assert [str(request.url).split("?")[0] for request in requests] == [
            f"https://api.example/sandboxes/{sandbox_id}/build-logs-url",
            target,
        ]
        assert requests[1].url.params["follow"] == ("true" if follow else "false")
        assert requests[1].url.params["format"] == ("sse" if follow else "text")
    finally:
        await api.close()


async def test_gap_flushes_pending_character_and_keeps_incompleteness_across_reconnect():
    gap = event(
        "gap",
        {"reason": "truncated", "requested_offset": "1", "first_available_offset": "4", "dropped_bytes": "3"},
        cursor(4),
    )
    api, requests, _ = setup([log(b"\xe2", 0) + gap, log(b"ok", 4) + done(6)])
    output: list[str] = []
    events: list[BuildLogEvent] = []
    result = await follow_build_logs(api, DISCOVERY, on_logs=output.append, on_event=events.append)
    assert output == ["�", "ok"]
    assert not result.output_complete
    assert requests[2].url.params["cursor"] == cursor(4)
    assert [item.type for item in events] == ["log", "gap", "log", "done"]


async def test_unlisted_gap_and_completion_reasons_pass_through_unchanged():
    gap = event(
        "gap",
        {"reason": "expired", "requested_offset": "0", "first_available_offset": "3", "dropped_bytes": "3"},
        cursor(3),
    )
    api, _, _ = setup([gap + log(b"ok", 3) + done(5, reason="superseded")])
    events: list[BuildLogEvent] = []
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, on_event=events.append)
    assert isinstance(events[0], BuildLogGapEvent)
    assert events[0].reason == "unknown"
    assert events[0].raw_reason == "expired"
    assert result.reason == "unknown"
    assert result.raw_reason == "superseded"
    assert not result.output_complete


async def test_diagnostics_are_visible_without_event_callback():
    api, _, _ = setup(
        [
            event("state", {"status": "building"})
            + event("unavailable", {"reason": "build logs unavailable"})
            + done(output_complete=False)
        ]
    )
    output: list[str] = []
    await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert "[build logs: state]" in output[0]
    assert "[build logs: unavailable]" in output[1]


async def test_callback_exception_retains_prior_checkpoint_and_never_retries():
    api, requests, streams = setup([log(b"a", 0) + log(b"b", 1) + done(2)])

    def callback(chunk: str) -> None:
        if chunk == "b":
            raise RuntimeError("application side effect failed")

    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=callback)
    assert failure.value.cursor == cursor(1)
    assert failure.value.code == "BUILD_LOG_CALLBACK_ERROR"
    assert len(requests) == 2 and streams[0].closed


async def test_event_callback_failure_does_not_acknowledge_gap():
    api, requests, _ = setup(
        [
            event(
                "gap",
                {
                    "reason": "truncated",
                    "requested_offset": "0",
                    "first_available_offset": "3",
                    "dropped_bytes": "3",
                },
                cursor(3),
            )
        ]
    )

    def callback(_event) -> None:
        raise ValueError("cannot persist")

    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, on_event=callback, cursor=cursor(0))
    assert failure.value.cursor == cursor(0)
    assert len(requests) == 2


async def test_keyboard_interrupt_closes_response_and_exposes_checkpoint():
    api, requests, streams = setup([log(b"a", 0) + log(b"b", 1)])

    def callback(chunk):
        if chunk == "b":
            raise KeyboardInterrupt()

    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=callback)
    assert failure.value.code == "BUILD_LOG_CANCELED" and failure.value.cursor == cursor(1)
    assert len(requests) == 2 and streams[0].closed


async def test_task_cancellation_closes_only_its_response_and_carries_checkpoint():
    hanging = Hanging([log(b"a", 0)])
    api, requests, _ = setup([sse(hanging)])
    delivered = anyio.Event()
    captured: list[BaseException] = []

    async def observe() -> None:
        def on_logs(_chunk: str) -> None:
            delivered.set()

        try:
            await follow_build_logs(api, DISCOVERY, on_logs=on_logs)
        except BaseException as exc:
            captured.append(exc)
            raise

    task = asyncio.ensure_future(observe())
    with anyio.fail_after(2):
        await delivered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Cancellation stays cancellation; the checkpoint rides on it.
    assert isinstance(captured[0], asyncio.CancelledError)
    interrupt = _interrupt_error_of(captured[0])
    assert isinstance(interrupt, BuildLogInterruptedError)
    assert interrupt.code == "BUILD_LOG_CANCELED" and interrupt.cursor == cursor(1)
    assert hanging.closed
    # The transport (and its borrowed pool) keeps serving other requests.
    assert await api.send(RequestSpec("GET", "/ordinary")) == {"ok": True}
    assert len(requests) == 3


@pytest.mark.parametrize(
    "name,payload,ident",
    [
        ("log", {"data_base64": "YQ==", "start_offset": "0", "next_offset": "2"}, cursor(2)),
        ("log", {"data_base64": "YQ==", "start_offset": 0, "next_offset": "1"}, cursor(1)),
        ("log", {"data_base64": "YR==", "start_offset": "0", "next_offset": "1"}, cursor(1)),
        ("log", {"data_base64": "YQ==", "start_offset": "00", "next_offset": "1"}, cursor(1)),
        (
            "log",
            {
                "data_base64": "YQ==",
                "start_offset": "18446744073709551615",
                "next_offset": "18446744073709551616",
            },
            cursor(1),
        ),
        ("log", {"data_base64": "YQ==", "start_offset": "0", "next_offset": "1"}, None),
        (
            "log",
            {
                "data_base64": base64.b64encode(b"a" * 65537).decode(),
                "start_offset": "0",
                "next_offset": "65537",
            },
            cursor(65537),
        ),
        ("state", {"status": 123}, None),
        ("state", {"status": "ready"}, cursor(0)),
        ("done", {"status": "ready", "reason": "terminal", "output_complete": "true"}, None),
        ("done", {"status": "ready", "reason": 7, "output_complete": True}, None),
        (
            "gap",
            {"reason": None, "requested_offset": "0", "first_available_offset": "3", "dropped_bytes": "3"},
            cursor(3),
        ),
        (
            "gap",
            {
                "reason": "truncated",
                "requested_offset": "0",
                "first_available_offset": "3",
                "dropped_bytes": "2",
            },
            cursor(3),
        ),
        ("unavailable", {}, None),
        ("new_unknown_event", {}, None),
    ],
)
async def test_malformed_protocol_never_retries(name, payload, ident):
    api, requests, _ = setup([event(name, payload, ident)])
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
    assert len(requests) == 2


async def test_cursor_contents_are_opaque_and_returned_unchanged():
    checkpoint = "another opaque checkpoint: +/="
    second = event(
        "log",
        {"data_base64": "Yg==", "start_offset": "1", "next_offset": "2"},
        checkpoint,
    )
    api, requests, _ = setup([log(b"a", 0) + second + done(cursor=checkpoint)])
    output: list[str] = []
    result = await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert output == ["a", "b"] and result.cursor == checkpoint
    assert len(requests) == 2


@pytest.mark.parametrize(
    "checkpoint", ["", "bad\x00cursor", "bad\rcursor", "bad\ncursor", "a\tb", "a\x7fb", "x" * 2049, 5]
)
async def test_invalid_caller_cursor_rejected_without_request(checkpoint):
    # One cursor rule in both SDKs: a caller's cursor that breaks it is
    # BadRequestError naming it, before any request.
    api, requests, _ = setup([])
    with pytest.raises(BadRequestError, match="^cursor must be"):
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, cursor=checkpoint)
    assert requests == []


async def test_a_server_cursor_with_a_tab_is_a_protocol_error():
    # The same rule for server-sent ids: TypeScript refuses it too.
    api, _, _ = setup(
        [event("log", {"data_base64": "eA==", "start_offset": "0", "next_offset": "1"}, "a\tb")]
    )
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"


async def test_resumed_cursor_establishes_offset_from_first_log_event():
    # Given an opaque resume checkpoint without a client-readable byte offset.
    api, requests, _ = setup([log(b"tail", 40) + done(44)])
    output: list[str] = []

    # When following from that checkpoint.
    result = await follow_build_logs(api, DISCOVERY, on_logs=output.append, cursor="resume checkpoint")

    # Then the public event offsets establish continuity and the cursor is sent as-is.
    assert output == ["tail"] and result.cursor == cursor(44)
    assert requests[1].url.params["cursor"] == "resume checkpoint"


async def test_resumed_cursor_still_rejects_later_noncontiguous_offsets():
    # Given a resumed stream whose second chunk skips bytes without a gap event.
    api, requests, _ = setup([log(b"a", 40) + log(b"b", 42)])
    output: list[str] = []

    # When observing the stream.
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=output.append, cursor="resume checkpoint")

    # Then only the acknowledged first chunk is retained, with no retry.
    assert output == ["a"] and failure.value.cursor == cursor(41)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR" and len(requests) == 2


async def test_resumed_cursor_accepts_gap_offsets_from_the_event():
    # Given an opaque checkpoint followed by an explicit gap.
    gap = event(
        "gap",
        {
            "reason": "truncated",
            "requested_offset": "40",
            "first_available_offset": "44",
            "dropped_bytes": "4",
        },
        cursor(44),
    )
    api, _, _ = setup([gap + log(b"a", 44) + done(45)])
    output: list[str] = []

    # When following from the checkpoint.
    result = await follow_build_logs(
        api, DISCOVERY, on_logs=output.append, on_event=lambda _: None, cursor="resume checkpoint"
    )

    # Then the gap remains visible through incomplete output.
    assert output == ["a"] and result.cursor == cursor(45) and not result.output_complete


@pytest.mark.parametrize("retryable", [False, True])
async def test_error_retry_uses_public_retryable_field_not_error_code(retryable):
    # Given an error with a code the SDK has never seen.
    wire = event("error", {"error": "try again", "code": "FUTURE_ERROR", "retryable": retryable})
    api, requests, _ = setup([wire, done()])

    # When following the stream.
    if retryable:
        result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, on_event=lambda _: None)
        # Then a retryable error reconnects.
        assert result.status == "ready" and len(requests) == 3
    else:
        with pytest.raises(BuildLogInterruptedError) as failure:
            await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, on_event=lambda _: None)
        # Then a terminal error surfaces unchanged without reconnecting.
        assert failure.value.code == "FUTURE_ERROR" and len(requests) == 2


async def test_empty_closed_source_can_checkpoint_zero():
    api, requests, _ = setup([done(0)])
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert result.cursor == cursor(0) and result.output_complete
    assert len(requests) == 2


async def test_unexpected_eof_is_bounded_even_after_repeated_state(monkeypatch):
    opening = event("state", {"status": "building", "deadline_at": "2000-01-01T00:00:00Z"})
    api, requests, streams = setup([opening] * 6)
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("daytona.v2.build_logs._sleep", record)
    with pytest.raises(BuildLogInterruptedError, match="reconnect budget"):
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert len(requests) == 7
    assert delays == [0.25, 0.5, 1, 2, 5]
    assert all(stream.closed for stream in streams)


async def test_retry_after_is_respected(monkeypatch):
    api, requests, _ = setup([httpx.Response(429, headers={"retry-after": "3"}), done()])
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("daytona.v2.build_logs._sleep", record)
    await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert delays == [3] and len(requests) == 3


async def test_cursor_progress_resets_only_the_consecutive_failure_budget():
    api, requests, _ = setup([b""] * 5 + [log(b"a", 0)] + [b""] * 4 + [done(1)])
    output: list[str] = []
    result = await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert result.cursor == cursor(1) and output == ["a"]
    assert len(requests) == 12


async def test_ten_healthy_seconds_reset_failure_budget_without_cursor_progress(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock[0])

    class Healthy(Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            clock[0] += 10
            yield b": heartbeat\n\n"

    api, requests, _ = setup([b""] * 5 + [sse(Healthy([]))] + [b""] * 4 + [done()])
    result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert result.status == "ready" and len(requests) == 12


async def test_transport_failure_reconnects_but_callback_connection_error_does_not():
    class Broken(Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield log(b"a", 0)
            raise httpx.ReadError("connection reset")

    api, requests, _ = setup([sse(Broken([])), log(b"b", 1) + done(2)])
    output: list[str] = []
    await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert output == ["a", "b"] and len(requests) == 3
    from daytona.v2.errors import ConnectionError

    def callback(_chunk):
        raise ConnectionError("application failure, not a transport failure")

    api, requests, _ = setup([log(b"x", 0)])
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=callback)
    assert failure.value.code == "BUILD_LOG_CALLBACK_ERROR" and len(requests) == 2


async def test_partial_trailing_event_does_not_commit_cursor():
    api, requests, _ = setup([log(b"a", 0) + log(b"b", 1)[:-2], log(b"b", 1) + done(2)])
    output: list[str] = []
    await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert output == ["a", "b"] and requests[2].url.params["cursor"] == cursor(1)


@pytest.mark.parametrize("tail", [b"data: " + b"x" * 131072, b"data: " + b"x" * 131072 + b"\n\n"])
def test_frame_bound_applies_to_complete_and_incomplete_events(tail):
    with pytest.raises(_ProtocolError, match="128 KiB"):
        list(_messages(iter([tail])))


def test_sse_crlf_and_multiline_json_byte_fragmentation():
    wire = b'event: state\r\ndata: {"status":"ready",\r\ndata: "error":null}\r\n\r\n'
    assert list(_messages(iter(bytes([byte]) for byte in wire))) == [
        ("state", None, {"status": "ready", "error": None})
    ]


@pytest.mark.parametrize(
    "body",
    [b'{"url": "\xff"}', b"\xef\xbb\xbf" + json.dumps({"url": LOGS_URL}).encode()],
    ids=["invalid UTF-8", "a leading BOM"],
)
async def test_a_discovery_body_that_is_not_strict_utf8_json_is_a_protocol_error(body: bytes) -> None:
    # As TypeScript decodes it: strict UTF-8, a BOM is not JSON.
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = AsyncTransport("https://api.example", "secret-test-key", client=client)
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _chunk: None)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
    assert str(failure.value) == "invalid build log discovery JSON"
    assert len(requests) == 1


async def test_unknown_event_is_rejected_before_delivery_or_retry():
    api, requests, _ = setup([event("other_log", {"data_base64": "eA=="})])
    output: list[str] = []
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=output.append)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
    assert output == [] and len(requests) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
async def test_auth_bad_cursor_and_not_found_never_fall_back(status):
    api, requests, _ = setup([httpx.Response(status, json={"message": "refused", "code": "BAD_REQUEST"})])
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, cursor=cursor(0))
    assert failure.value.status_code == status and failure.value.cursor == cursor(0)
    assert len(requests) == 2


async def test_http_error_reads_unified_router_envelope():
    envelope = {
        "code": "NOT_FOUND",
        "message": "the requested region is unavailable",
        "source": "DAYTONA_ROUTER",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
    }
    api, requests, _ = setup([httpx.Response(404, json=envelope)])
    with pytest.raises(NotFoundError) as failure:
        await read_build_logs(api, DISCOVERY)
    error = failure.value
    assert error.status_code == 404
    assert error.code == "NOT_FOUND"
    assert error.source == "DAYTONA_ROUTER"
    assert error.message == "the requested region is unavailable"
    assert error.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert len(requests) == 2


async def test_http_error_reads_error_and_code_fields():
    api, _, _ = setup(
        [httpx.Response(409, json={"error": "sandbox is not running", "code": "SANDBOX_NOT_RUNNING"})]
    )
    with pytest.raises(DaytonaError) as failure:
        await read_build_logs(api, DISCOVERY)
    error = failure.value
    assert error.status_code == 409
    assert error.code == "SANDBOX_NOT_RUNNING"
    assert error.message == "sandbox is not running"


async def test_http_error_without_prose_keeps_build_log_status_fallback():
    api, _, _ = setup([httpx.Response(500, json={"code": "INTERNAL_SERVER_ERROR"})])
    with pytest.raises(DaytonaError) as failure:
        await read_build_logs(api, DISCOVERY)
    error = failure.value
    assert error.status_code == 500
    assert error.code == "INTERNAL_SERVER_ERROR"
    assert error.message == "build log HTTP 500"


async def test_http_error_with_malformed_prose_does_not_become_a_protocol_error():
    api, _, _ = setup([httpx.Response(503, json={"message": 123, "code": "SERVICE_UNAVAILABLE"})])
    with pytest.raises(DaytonaError) as failure:
        await read_build_logs(api, DISCOVERY)
    error = failure.value
    assert error.status_code == 503
    assert error.code == "SERVICE_UNAVAILABLE"
    assert error.message == "build log HTTP 503"


@pytest.mark.parametrize("body", [{"error": ""}, {"message": ""}])
async def test_http_error_empty_prose_keeps_build_log_status_fallback(body):
    api, _, _ = setup([httpx.Response(500, json={**body, "code": "INTERNAL_SERVER_ERROR"})])
    with pytest.raises(DaytonaError) as failure:
        await read_build_logs(api, DISCOVERY)
    error = failure.value
    assert error.code == "INTERNAL_SERVER_ERROR"
    assert error.message == "build log HTTP 500"


async def test_http_error_surfaces_region_and_drops_non_string_region():
    api, _, _ = setup(
        [
            httpx.Response(
                400,
                json={
                    "code": "UNKNOWN_REGION",
                    "message": "nope",
                    "source": "DAYTONA_ROUTER",
                    "region": "narnia",
                },
            )
        ]
    )
    with pytest.raises(DaytonaError) as ok:
        await read_build_logs(api, DISCOVERY)
    assert ok.value.region == "narnia"

    api2, _, _ = setup(
        [
            httpx.Response(
                400,
                json={"code": "UNKNOWN_REGION", "message": "nope", "source": "DAYTONA_ROUTER", "region": 5},
            )
        ]
    )
    with pytest.raises(DaytonaError) as non_string:
        await read_build_logs(api2, DISCOVERY)
    assert non_string.value.region is None


async def test_http_error_surfaces_router_per_code_extras():
    envelope = {
        "code": "REGION_MISMATCH",
        "message": "two regions",
        "source": "DAYTONA_ROUTER",
        "stated": "region-a",
        "actual": "region-other",
    }
    api, _, _ = setup([httpx.Response(400, json=envelope)])
    with pytest.raises(DaytonaError) as failure:
        await read_build_logs(api, DISCOVERY)
    error = failure.value
    assert error.code == "REGION_MISMATCH"
    assert error.stated == "region-a"
    assert error.actual == "region-other"


async def test_source_changed_stops_without_zero_restart():
    api, requests, _ = setup([event("source_changed", {"reason": "replacement"})])
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, cursor=cursor(3))
    assert failure.value.code == "BUILD_LOG_SOURCE_CHANGED" and failure.value.cursor == cursor(3)
    assert len(requests) == 2


@pytest.mark.parametrize(
    "url",
    [
        "//evil.example/logs",
        "ftp://evil.example/logs",
        "https://user:password@logs.example/logs",
        "https://logs.example/logs?q=x",
        "https://logs.example/logs#x",
        "https://logs.example/\\evil",
        "https://logs.example/\nlogs",
    ],
)
async def test_invalid_discovery_target_never_receives_credentials(url):
    api, requests, _ = setup([], url=url)
    with pytest.raises(BuildLogInterruptedError):
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "/sandboxes/sbx/build-logs",
        "https://logs.example/images/image/build-logs",
        "https://logs.example/arbitrary",
        "https://logs.example/build-logs/images/other",
        "https://logs.example/build-logs/sandboxes/other",
        "https://logs.example/build-logs/other/nested/image",
        "https://logs.example/build-logs//image",
        "https://logs.example/build-logs/other%2Fnested/image",
        LOGS_URL + "/",
    ],
)
@pytest.mark.parametrize("follow", [False, True])
async def test_noncanonical_destination_refused_without_forwarding_credentials(follow, url):
    api, requests, _ = setup([], url=url)
    with pytest.raises(DaytonaError) as failure:
        if follow:
            await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
        else:
            await read_build_logs(api, DISCOVERY)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
    assert len(requests) == 1


async def test_redirects_do_not_forward_credentials_even_with_redirect_enabled_client():
    api, requests, _ = setup([httpx.Response(307, headers={"location": "https://evil.example/steal"})])
    with pytest.raises(BuildLogInterruptedError):
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
    assert len(requests) == 2


@pytest.mark.parametrize(
    "collection,requested,actual",
    [
        ("images", UUID.upper(), UUID),
        ("images", UUID.replace("-", ""), UUID),
        ("images", "{" + UUID.upper() + "}", UUID),
        ("images", "urn:uuid:" + UUID, UUID),
        ("sandboxes", UUID, "3FP9SaFP7qfRTj4GM9xtju-region-a"),
        ("sandboxes", "3FP9SaFP7qfRTj4GM9xtju", "3FP9SaFP7qfRTj4GM9xtju-region-a"),
        ("sandboxes", "3FP9SaFP7qfRTj4GM9xtju-region-a", UUID),
        ("sandboxes", "00000000-0000-0000-0000-000000000000", "1111111111111111-region-a"),
        ("sandboxes", UUID, "3FP9SaFP7qfRTj4GM9xtju-A_0-" + "r" * 60),
    ],
)
@pytest.mark.parametrize("follow", [False, True])
async def test_discovery_accepts_equivalent_resource_identity(collection, requested, actual, follow):
    target = f"https://logs.example/prefix/build-logs/{collection}/{actual}"
    api, requests, _ = setup([done() if follow else httpx.Response(200, text="state: ready\n")], url=target)
    path = f"/{collection}/{quote(requested, safe='')}/build-logs-url"
    if follow:
        assert (await follow_build_logs(api, path, on_logs=lambda _: None)).status == "ready"
    else:
        assert await read_build_logs(api, path) == "state: ready\n"
    assert str(requests[1].url).split("?")[0] == target
    assert requests[1].headers["authorization"] == "Bearer secret-test-key"


@pytest.mark.parametrize("collection", ["images", "sandboxes"])
@pytest.mark.parametrize("follow", [False, True])
async def test_discovery_accepts_another_single_segment_collection(collection, follow):
    # Given a discovered URL whose collection differs from the discovery route.
    target = f"https://logs.example/prefix/build-logs/other/{UUID}"
    api, requests, _ = setup([done() if follow else httpx.Response(200, text="state: ready\n")], url=target)
    path = f"/{collection}/{UUID}/build-logs-url"

    # When reading or following the validated resource.
    if follow:
        result = (await follow_build_logs(api, path, on_logs=lambda _: None)).status
    else:
        result = await read_build_logs(api, path)

    # Then discovery controls the collection, while resource identity is retained.
    assert result == ("ready" if follow else "state: ready\n")
    assert str(requests[1].url).split("?")[0] == target
    assert requests[1].headers["authorization"] == "Bearer secret-test-key"


@pytest.mark.parametrize(
    "collection,requested,target_path",
    [
        ("images", UUID.upper(), "/images/00000000-0000-0000-0000-000000000000"),
        ("images", UUID, "/sandboxes/3FP9SaFP7qfRTj4GM9xtju-region-a"),
        ("sandboxes", UUID, "/images/00000000-0000-0000-0000-000000000000"),
        ("sandboxes", UUID, "/sandboxes/1111111111111111-region-a"),
        ("sandboxes", "3FP9SaFP7qfRTj4GM9xtju-region-a", "/sandboxes/3FP9SaFP7qfRTj4GM9xtju-region-b"),
        # The SDK accepts region tags of 1-64 ASCII [A-Za-z0-9_-] characters.
        ("sandboxes", UUID, "/sandboxes/3FP9SaFP7qfRTj4GM9xtju-us.west"),
        ("sandboxes", UUID, "/sandboxes/3FP9SaFP7qfRTj4GM9xtju-" + "r" * 65),
        ("sandboxes", "3FP9SaFP7qfRTj4GM9xtju-us.west", f"/sandboxes/{UUID}"),
    ],
)
@pytest.mark.parametrize("follow", [False, True])
async def test_discovery_rejects_different_resource_identity(collection, requested, target_path, follow):
    api, requests, _ = setup([], url="https://logs.example/build-logs" + target_path)
    path = f"/{collection}/{quote(requested, safe='')}/build-logs-url"
    with pytest.raises(DaytonaError) as failure:
        if follow:
            await follow_build_logs(api, path, on_logs=lambda _: None)
        else:
            await read_build_logs(api, path)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
    assert len(requests) == 1


async def test_one_shot_rejects_oversized_chunked_bytes_and_closes_without_draining():
    # Three MiB in bytes, but far fewer Unicode characters: the limit must
    # count bytes rather than decoded text, even without Content-Length.
    page = "😀".encode() * (16 * 1024)
    stream = Chunks([page] * 48)
    reply = httpx.Response(200, headers={"content-type": "text/plain"}, stream=stream)
    api, requests, _ = setup([reply])
    with pytest.raises(DaytonaError, match="one-shot build log response exceeds 2 MiB") as failure:
        await read_build_logs(api, DISCOVERY)
    assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
    assert stream.closed and reply.is_closed
    assert 0 < stream.yielded < 48
    assert len(requests) == 2


async def test_one_shot_accepts_large_body_with_utf8_and_bom_parity():
    # A large output body, plus state text and a BOM.
    # The BOM, emoji and incomplete final character span transport chunks.
    prefix = b"\xbfstate: ready\n"
    tail = b"x" * (1024 * 1024 - 7)
    stream = Chunks(
        [
            b"\xef",
            b"\xbb",
            prefix,
            tail,
            b"\xf0",
            b"\x9f\x98",
            b"\x80\xff\xe2",
            b"\x82",
        ]
    )
    reply = httpx.Response(200, headers={"content-type": "text/plain"}, stream=stream)
    api, requests, _ = setup([reply])
    expected = "\ufeffstate: ready\n" + "x" * len(tail) + "😀��"
    assert await read_build_logs(api, DISCOVERY) == expected
    assert stream.closed and reply.is_closed
    assert stream.yielded == len(stream.chunks)
    assert len(requests) == 2


async def test_one_shot_byte_ceiling_is_inclusive():
    stream = Chunks([b"a" * (64 * 1024)] * 32)
    reply = httpx.Response(200, headers={"content-type": "text/plain"}, stream=stream)
    api, requests, _ = setup([reply])
    assert await read_build_logs(api, DISCOVERY) == "a" * (2 * 1024 * 1024)
    assert stream.closed and reply.is_closed
    assert len(requests) == 2


async def test_server_deadlines_remain_visible_as_metadata_through_terminal_state():
    events: list[BuildLogStateEvent] = []

    def on_event(item: BuildLogEvent) -> None:
        assert isinstance(item, BuildLogStateEvent)
        events.append(item)

    state = _Observer(lambda _: None, on_event, None)
    await state.accept("state", None, {"status": "building", "deadline_at": "2999-01-01T00:00:00Z"})
    await state.accept("state", None, {"status": "ready", "deadline_at": "2000-01-01T00:00:00Z"})
    assert [item.deadline_at for item in events] == [
        datetime(2999, 1, 1, tzinfo=timezone.utc),
        datetime(2000, 1, 1, tzinfo=timezone.utc),
    ]
    assert [item.status for item in events] == ["building", "ready"]


@pytest.mark.parametrize("deadline", ["not-a-date", "2026-01-01T00:00:00"])
async def test_server_deadline_metadata_is_still_validated(deadline):
    output: list[str] = []
    events: list[object] = []
    state = _Observer(output.append, events.append, None)
    # Malformed dates can fail wire-model validation before the timezone check.
    # Both layers must refuse the protocol value before invoking callbacks.
    with pytest.raises(_ProtocolError):
        await state.accept("state", None, {"status": "building", "deadline_at": deadline})
    assert output == []
    assert events == []


@pytest.mark.parametrize("status", [200, 401, 403])
async def test_real_http_one_shot_dribbling_body_is_bounded_and_preserves_http_denial(status):
    requests = []
    stop = threading.Event()
    disconnected = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path.endswith("build-logs-url"):
                body = discovery_body(self)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(b"tail" if status == 200 else b'{"error":')
                self.wfile.flush()
                while not stop.wait(0.02):
                    self.wfile.write(b" ")
                    self.wfile.flush()
            except OSError:
                disconnected.set()
            self.close_connection = True

    with local_http_server(Handler) as base:
        api = AsyncTransport(
            base, "test-key", request_timeout=0.15, stream_client_factory=local_stream_client
        )
        try:
            with anyio.fail_after(2):
                with pytest.raises(DaytonaError) as failure:
                    await read_build_logs(api, DISCOVERY)
            if status == 200:
                from daytona.v2.errors import ClientTimeoutError

                assert isinstance(failure.value, ClientTimeoutError)
                assert failure.value.code == "REQUEST_TIMEOUT"
            else:
                assert failure.value.status_code == status
                assert failure.value.code != "REQUEST_TIMEOUT"
            assert requests == [DISCOVERY, "/build-logs/images/image?follow=false&format=text"]
            assert await anyio.to_thread.run_sync(disconnected.wait, 1), (
                "one-shot body was not closed at its deadline"
            )
        finally:
            stop.set()
            await api.close()


async def test_caller_timeout_not_restarted_by_reconnect(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: clock[0])

    async def advance(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr("daytona.v2.build_logs._sleep", advance)
    api, requests, streams = setup([b""] * 6)
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, timeout=0.6)
    assert failure.value.code == "REQUEST_TIMEOUT" and clock[0] == 0.6
    assert len(requests) == 3 and all(stream.closed for stream in streams)


async def test_caller_timeout_cancels_a_blocked_callback_and_keeps_checkpoint():
    api, requests, streams = setup([log(b"a", 0) + log(b"b", 1) + done(2)])

    async def on_logs(chunk: str) -> None:
        if chunk == "b":
            await anyio.sleep(30)

    started = time.monotonic()
    with pytest.raises(BuildLogInterruptedError) as failure:
        await follow_build_logs(api, DISCOVERY, on_logs=on_logs, timeout=0.2)
    assert time.monotonic() - started < 2
    assert failure.value.code == "REQUEST_TIMEOUT" and failure.value.cursor == cursor(1)
    assert len(requests) == 2 and streams[0].closed


@pytest.mark.parametrize("timeout", [-1.0, float("inf"), float("nan")])
async def test_invalid_caller_timeout_is_rejected_before_any_request(timeout):
    api, requests, _ = setup([])
    with pytest.raises(BadRequestError, match="timeout must be a non-negative finite number") as excinfo:
        await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None, timeout=timeout)
    assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )
    assert requests == []


async def test_real_http_timeout_closes_blocked_body_and_retains_checkpoint():
    requests: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path.endswith("build-logs-url"):
                body = discovery_body(self)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(log(b"a", 0))
                self.wfile.flush()  # Leave the response open, without done.

    with local_http_server(Handler) as base:
        api = AsyncTransport(base, "test-key")
        output: list[str] = []
        try:
            with pytest.raises(BuildLogInterruptedError) as failure:
                await follow_build_logs(api, DISCOVERY, on_logs=output.append, timeout=0.25)
            assert failure.value.code == "REQUEST_TIMEOUT"
            assert failure.value.cursor == cursor(1) and output == ["a"]
            assert len(requests) == 2 and all(auth == "Bearer test-key" for _, auth in requests)
        finally:
            await api.close()


# --- the generated synchronous surface -------------------------------------

requires_sigint = pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery differs on Windows")


@contextlib.contextmanager
def sync_client(
    responses: list[httpx.Response], *, url: str
) -> Iterator[tuple[Daytona, list[httpx.Request]]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("build-logs-url"):
            return httpx.Response(200, json={"url": url})
        return responses.pop(0)

    client = Daytona(DaytonaConfig(api_key="k"))
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    impl = client._impl
    original = impl._api
    impl._api = AsyncTransport("http://api.invalid", "k", client=mock)
    impl.images = AsyncImagesService(impl._api)
    try:
        yield client, requests
    finally:

        async def release() -> None:
            await mock.aclose()
            await original.close()

        client._bridge.call(release)
        client.close()


def test_sync_surface_reads_and_follows_through_discovery():
    target = "https://logs.example/build-logs/sandboxes/sbx"
    responses = [httpx.Response(200, text="state: ready\nok\n"), sse(Chunks([log(b"ok\n", 0) + done(3)]))]
    with sync_client(responses, url=target) as (client, requests):
        sandbox = lazy_sandbox(client, "sbx")
        assert sandbox.build_logs() == "state: ready\nok\n"
        output: list[str] = []
        result = sandbox.follow_build_logs(on_logs=output.append)
        assert output == ["ok\n"] and result.cursor == cursor(3)
    assert [str(request.url).split("?")[0] for request in requests] == [
        "http://api.invalid/sandboxes/sbx/build-logs-url",
        target,
        "http://api.invalid/sandboxes/sbx/build-logs-url",
        target,
    ]


def test_sync_images_namespace_follows_build_logs():
    with sync_client([sse(Chunks([log(b"x", 0) + done(1)]))], url=LOGS_URL) as (client, requests):
        output: list[str] = []
        result = client.images.follow_build_logs("image", on_logs=output.append)
        assert output == ["x"] and result.cursor == cursor(1)
    assert str(requests[0].url) == "http://api.invalid/images/image/build-logs-url"


@requires_sigint
def test_sync_ctrl_c_surfaces_canceled_checkpoint_and_closes_the_stream():
    hanging = Hanging([log(b"a", 0)])
    delivered = threading.Event()
    with sync_client([sse(hanging)], url="https://logs.example/build-logs/sandboxes/sbx") as (client, _):
        sandbox = lazy_sandbox(client, "sbx")

        def on_logs(_chunk: str) -> None:
            # SIGINT lands on the main thread, which is blocked in the bridge.
            threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
            delivered.set()

        started = time.monotonic()
        try:
            with pytest.raises(BuildLogInterruptedError) as failure:
                sandbox.follow_build_logs(on_logs=on_logs)
        except KeyboardInterrupt:
            pytest.fail("KeyboardInterrupt escaped instead of BUILD_LOG_CANCELED")
        assert delivered.is_set() and time.monotonic() - started < 3
        assert failure.value.code == "BUILD_LOG_CANCELED" and failure.value.cursor == cursor(1)
        assert isinstance(failure.value.__cause__, KeyboardInterrupt)
        assert hanging.closed

        # The bridge keeps working after the interrupt.
        async def answer() -> int:
            await anyio.lowlevel.checkpoint()
            return 42

        assert client._bridge.call(answer) == 42


@pytest.mark.parametrize(("budget", "completes"), [(3_000_000, True), (2_000_000, False)])
async def test_a_budget_above_the_timer_maximum_is_no_deadline(monkeypatch, budget, completes):
    # The clock moves 30 days once the first chunk is delivered: 2,000,000 s
    # (23 days) is a real deadline that then passes, 3,000,000 s (above the
    # timer maximum) is none. The TypeScript twin is tests/unbounded-waits.test.ts.
    now = [0.0]
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: now[0])
    api, _, _ = setup([log(b"a", 0) + log(b"b", 1) + done(2)])
    output: list[str] = []

    def on_logs(chunk: str) -> None:
        output.append(chunk)
        now[0] += 30 * 86_400.0

    if completes:
        result = await follow_build_logs(api, DISCOVERY, on_logs=on_logs, timeout=budget)
        assert output == ["a", "b"] and result.output_complete
    else:
        with pytest.raises(BuildLogInterruptedError) as failure:
            await follow_build_logs(api, DISCOVERY, on_logs=on_logs, timeout=budget)
        assert failure.value.code == "REQUEST_TIMEOUT"


@pytest.mark.parametrize(("depth", "accepted"), [(600, True), (601, False)])
async def test_build_log_json_nested_past_the_shared_depth_limit_is_a_protocol_error(depth, accepted):
    # Both SDKs refuse JSON nested deeper than JSON_MAX_DEPTH (600): a done
    # event (depth 1) carrying an unknown field nested to `depth`. The
    # TypeScript twin is in tests/build-logs.test.ts.
    nested = "[" * (depth - 1) + "]" * (depth - 1)
    frame = (
        'event: done\ndata: {"status": "ready", "reason": "terminal", "output_complete": true, '
        f'"extra": {nested}}}\n\n'
    ).encode()
    api, _, _ = setup([frame])
    if accepted:
        result = await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
        assert result.status == "ready"
    else:
        with pytest.raises(BuildLogInterruptedError) as failure:
            await follow_build_logs(api, DISCOVERY, on_logs=lambda _: None)
        assert failure.value.code == "BUILD_LOG_PROTOCOL_ERROR"
