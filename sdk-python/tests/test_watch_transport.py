# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Directory watches over a real HTTP transport: a close-delimited SSE body (no
Content-Length, the connection closes when the stream ends) and one that ends
short of its Content-Length."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import pytest

from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import ConnectionError
from daytona.v2.filesystem import AsyncFileSystemService

_EVENT = json.dumps({"type": "create", "name": "a", "isDir": False, "timestamp": 1})


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        case = self.path.split("/")[1]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if case == "truncated":
            self.send_header("Content-Length", "100000")
        self.end_headers()
        if case in ("burst", "early"):
            # start and ten events in ONE write (early: two events before
            # start), then the stream stays open until the client closes it.
            events = [
                f"event: fs\ndata: {json.dumps({'type': 'create', 'name': f'e{index}', 'isDir': False, 'timestamp': 1})}\n\n"
                for index in range(10)
            ]
            start = "event: start\ndata: {}\n\n"
            frames = [start, *events] if case == "burst" else [*events[:2], start, *events[2:]]
            self.wfile.write("".join(frames).encode())
            self.wfile.flush()
            with contextlib.suppress(OSError):
                self.rfile.read(1)
            return
        self.wfile.write(f"event: start\ndata: {{}}\n\nevent: fs\ndata: {_EVENT}\n\n".encode())
        self.wfile.flush()
        self.close_connection = True


@pytest.fixture
def base() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.anyio
async def test_a_stop_racing_the_readers_close_ends_the_watch_without_an_error(base: str) -> None:
    transport = AsyncTransport(f"{base}/eof")
    entered, release = asyncio.Event(), asyncio.Event()
    exits: list[Optional[Exception]] = []

    async def on_event(_event: object) -> None:
        entered.set()
        await release.wait()

    try:
        handle = await AsyncFileSystemService(transport).watch_dir("/", on_event, on_exit=exits.append)
        await asyncio.wait_for(entered.wait(), timeout=5)
        # The body has ended; stop() closes the stream while the callback
        # runs, and the reader closes it again once the callback returns.
        stopping = asyncio.ensure_future(handle.stop())
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.wait_for(stopping, timeout=5)
    finally:
        await transport.close()

    assert handle.exit_error is None
    assert exits == [None]


@pytest.mark.anyio
async def test_concurrent_closes_of_one_response_stream_share_one_release(base: str) -> None:
    transport = AsyncTransport(f"{base}/eof")
    try:
        stream = await transport.open_stream(RequestSpec("GET", "/"))
        await asyncio.gather(stream.close(), stream.close(), stream.close())
        await stream.close()
    finally:
        await transport.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "message"),
    [("eof", "filesystem watch stream ended unexpectedly"), ("truncated", None)],
)
async def test_a_watch_whose_stream_drops_ends_with_a_connection_error(
    base: str, case: str, message: Optional[str]
) -> None:
    transport = AsyncTransport(f"{base}/{case}")
    try:
        handle = await AsyncFileSystemService(transport).watch_dir("/", lambda _event: None)
        error = await asyncio.wait_for(handle.wait_for_exit(), timeout=5)
    finally:
        await transport.close()

    assert isinstance(error, ConnectionError)
    assert error.source == "SDK"
    if message is not None:
        assert str(error) == message


def _refused_url() -> str:
    """A URL nothing listens on: an ephemeral port reserved, then released."""
    import socket

    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    return f"http://127.0.0.1:{port}"


class _SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/w404"):
            body = json.dumps(
                {"code": "FILE_NOT_FOUND", "message": "no such path", "source": "DAYTONA_DAEMON"}
            )
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b": no start frame\n\n")
        self.wfile.flush()
        self.close_connection = True


@pytest.fixture
def setup_base() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SetupHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["refused", "w404", "nostart"])
async def test_a_watch_that_fails_before_start_raises_without_calling_on_exit(
    setup_base: str, case: str
) -> None:
    from daytona.v2.errors import DaytonaError

    base = _refused_url() if case == "refused" else f"{setup_base}/{case}"
    transport = AsyncTransport(base)
    exits: list[Optional[Exception]] = []
    try:
        with pytest.raises(DaytonaError) as info:
            await AsyncFileSystemService(transport).watch_dir("/", lambda _event: None, on_exit=exits.append)
    finally:
        await transport.close()

    expected = {"refused": ConnectionError, "w404": DaytonaError, "nostart": ConnectionError}[case]
    assert isinstance(info.value, expected)
    if case == "w404":
        assert info.value.status_code == 404
    await asyncio.sleep(0.05)
    assert exits == []


@pytest.mark.anyio
@pytest.mark.parametrize("after_start", [False, True])
async def test_cancelling_watch_dir_before_it_returns_calls_no_exit_callback(after_start: bool) -> None:
    """Cancelled before watch_dir() returns, before the start frame or after
    the reader saw it but before watch_dir() resumed: no exit callback."""
    from daytona.v2.filesystem import AsyncFileSystemService as Service

    outer: list[asyncio.Task[object]] = []
    reading = asyncio.Event()

    class Stream:
        def __init__(self) -> None:
            self.closed = asyncio.Event()

        async def iter_lines(self):
            reading.set()
            if after_start:
                for line in ("event: start", "data: {}", ""):
                    yield line
                # The reader set `ready` and came back for the next line;
                # watch_dir() has not resumed yet.
                outer[0].cancel()
            await self.closed.wait()

        async def close(self) -> None:
            self.closed.set()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> Stream:
            return Stream()

    exits: list[Optional[Exception]] = []
    task = asyncio.ensure_future(Service(Transport()).watch_dir("/", lambda _e: None, on_exit=exits.append))  # type: ignore[arg-type]
    outer.append(task)
    await asyncio.wait_for(reading.wait(), timeout=5)
    if not after_start:
        task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert exits == []


async def _stop_from_the_first_callback(url: str) -> tuple[list[str], Optional[Exception], list[str]]:
    transport = AsyncTransport(url)
    seen: list[str] = []
    early: list[str] = []
    returned = False

    async def on_event(event: object) -> None:
        name = event.name
        if not returned:
            early.append(name)
        seen.append(name)
        await handle.stop()

    try:
        handle = await AsyncFileSystemService(transport).watch_dir("/", on_event)
        returned = True
        error = await asyncio.wait_for(handle.wait_for_exit(), timeout=5)
    finally:
        await transport.close()
    return early, error, seen


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["burst", "early"])
async def test_no_callback_runs_before_watch_dir_returns_so_a_callback_can_stop_its_own_watch(
    base: str, case: str
) -> None:
    """start and the first events arrive in one write (early: two events even
    precede start); the documented pattern, a callback awaiting its own
    handle's stop(), ends every run cleanly after the first event."""
    for _ in range(20):
        assert await _stop_from_the_first_callback(f"{base}/{case}") == ([], None, ["e0"])


def _sync_events_before_the_handle(bridge: object, transport: AsyncTransport) -> list[str]:
    from daytona.v2._sync import filesystem as sync_fs

    built: list[object] = []
    before_handle: list[str] = []
    delivered = threading.Event()
    original_init = sync_fs.WatchHandle.__init__

    def recording_init(self: sync_fs.WatchHandle, *args: object) -> None:
        original_init(self, *args)  # type: ignore[arg-type]
        built.append(self)

    def on_event(event: object) -> None:
        if not built:
            before_handle.append(event.name)
        delivered.set()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sync_fs.WatchHandle, "__init__", recording_init)
        handle = sync_fs.FileSystemService(AsyncFileSystemService(transport), bridge).watch_dir("/", on_event)  # type: ignore[arg-type]
    assert delivered.wait(5)
    handle.stop()
    return before_handle


def test_a_sync_watch_delivers_nothing_before_its_handle_is_built(base: str) -> None:
    """The synchronous watch_dir() releases the callbacks only once it has
    built its WatchHandle (the caller may still be storing it: see the docs)."""
    from daytona.v2._bridge import SyncBridge

    bridge = SyncBridge()
    try:
        transport = bridge.call(lambda: _transport(f"{base}/burst"))
        for _ in range(20):
            assert _sync_events_before_the_handle(bridge, transport) == []
        bridge.call(transport.close)
    finally:
        bridge.close()


async def _transport(url: str) -> AsyncTransport:
    return AsyncTransport(url)


def test_a_sync_callback_can_stop_its_own_watch_through_a_caller_thread_event(base: str) -> None:
    """The race-free way the docs give to end a synchronous watch from an event."""
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.filesystem import FileSystemService

    bridge = SyncBridge()
    try:
        transport = bridge.call(lambda: _transport(f"{base}/burst"))
        stop_requested = threading.Event()
        exits: list[Optional[Exception]] = []
        handle = FileSystemService(AsyncFileSystemService(transport), bridge).watch_dir(
            "/", lambda _event: stop_requested.set(), on_exit=exits.append
        )
        assert stop_requested.wait(5)
        handle.stop()
        deadline = time.monotonic() + 5
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert exits == [None]
        bridge.call(transport.close)
    finally:
        bridge.close()
