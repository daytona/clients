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
from daytona.v2.types import FilesystemEvent

_EVENT = json.dumps({"type": "create", "name": "a", "isDir": False, "timestamp": 1})


_CLOSED_BY_CLIENT = threading.Event()


def _event_data_totalling(total: int) -> list[str]:
    """Two fs event payloads (``e0``, ``e1`` padded) of ``total`` UTF-8 bytes together."""

    def payload(name: str) -> str:
        return json.dumps(
            {"type": "create", "name": name, "isDir": False, "timestamp": 1}, separators=(",", ":")
        )

    first = payload("e0")
    padding = total - len(first.encode()) - len(payload("e1").encode())
    return [first, payload("e1" + "x" * padding)]


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
        if case.startswith("bytes-"):
            # Two fs events before start whose data adds up to exactly N
            # UTF-8 bytes, then start; the stream stays open as for flood-.
            frames = _event_data_totalling(int(case.removeprefix("bytes-")))
            with contextlib.suppress(OSError):
                self.wfile.write(
                    (
                        "".join(f"event: fs\ndata: {data}\n\n" for data in frames)
                        + "event: start\ndata: {}\n\n"
                    ).encode()
                )
                self.wfile.flush()
                self.rfile.read(1)
            _CLOSED_BY_CLIENT.set()
            return
        if case.startswith(("flood-", "oflood-")):
            # N fs (oflood: overflow) events before start, then start; the
            # stream stays open until the client closes it, which the test is
            # told about.
            kind, count = case.split("-")
            events = (
                "event: overflow\ndata: {}\n\n" * int(count)
                if kind == "oflood"
                else "".join(
                    f"event: fs\ndata: {json.dumps({'type': 'create', 'name': f'e{index}', 'isDir': False, 'timestamp': 1})}\n\n"
                    for index in range(int(count))
                )
            )
            with contextlib.suppress(OSError):
                self.wfile.write((events + "event: start\ndata: {}\n\n").encode())
                self.wfile.flush()
                self.rfile.read(1)
            _CLOSED_BY_CLIENT.set()
            return
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
async def test_a_close_racing_an_owned_streams_release_waits_for_it(base: str) -> None:
    """An exclusive stream marks itself closed before its release; a second
    close() must still wait for that release, not return while it runs."""
    transport = AsyncTransport(f"{base}/eof")
    try:
        stream = await transport.open_exclusive_stream(RequestSpec("GET", "/"))
        owned = stream._owned_client
        assert owned is not None
        original = owned.aclose
        released: list[str] = []

        async def slow_aclose() -> None:
            await asyncio.sleep(0.1)
            await original()
            released.append("released")

        owned.aclose = slow_aclose  # type: ignore[method-assign]
        first = asyncio.ensure_future(stream.close())
        await asyncio.sleep(0.01)  # the first close() is inside its release
        await stream.close()
        assert released == ["released"]
        await first
    finally:
        await transport.close()


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

    async def on_event(event: FilesystemEvent) -> None:
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

    def on_event(event: FilesystemEvent) -> None:
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


async def _stop_through_a_signal(
    url: str, wrap: str
) -> tuple[Optional[Exception], list[Optional[Exception]]]:
    """The documented way to stop a watch from an event: the callback signals,
    the code holding the handle calls stop(). It must hold however
    watch_dir() is awaited."""
    transport = AsyncTransport(url)
    stop_requested = asyncio.Event()
    exits: list[Optional[Exception]] = []

    def on_event(_event: object) -> None:
        stop_requested.set()

    try:
        call = AsyncFileSystemService(transport).watch_dir("/", on_event, on_exit=exits.append)
        if wrap == "wait_for":
            handle = await asyncio.wait_for(call, timeout=5)
        else:
            handle, _ = await asyncio.gather(call, asyncio.sleep(0))
        await asyncio.wait_for(stop_requested.wait(), timeout=5)
        await handle.stop()
        error = await asyncio.wait_for(handle.wait_for_exit(), timeout=5)
    finally:
        await transport.close()
    return error, exits


@pytest.mark.anyio
@pytest.mark.parametrize("wrap", ["wait_for", "gather"])
@pytest.mark.parametrize("case", ["burst", "early"])
async def test_a_signalled_stop_ends_a_task_wrapped_watch_cleanly(base: str, wrap: str, case: str) -> None:
    for _ in range(20):
        assert await _stop_through_a_signal(f"{base}/{case}", wrap) == (None, [None])


@pytest.mark.anyio
async def test_a_watch_holds_4096_events_before_start_and_delivers_them_in_order(base: str) -> None:
    transport = AsyncTransport(f"{base}/flood-4096")
    seen: list[str] = []
    all_seen = asyncio.Event()

    def on_event(event: FilesystemEvent) -> None:
        seen.append(event.name)
        if len(seen) == 4096:
            all_seen.set()

    try:
        handle = await AsyncFileSystemService(transport).watch_dir("/", on_event)
        await asyncio.wait_for(all_seen.wait(), timeout=30)
        await handle.stop()
    finally:
        await transport.close()
    assert seen == [f"e{index}" for index in range(4096)]


@pytest.mark.anyio
async def test_a_watch_stream_with_4097_events_before_start_is_refused_without_an_exit_callback(
    base: str,
) -> None:
    from daytona.v2.errors import BadRequestError

    _CLOSED_BY_CLIENT.clear()
    transport = AsyncTransport(f"{base}/flood-4097")
    exits: list[Optional[Exception]] = []
    seen: list[object] = []
    try:
        with pytest.raises(BadRequestError, match="more than 4096 events before start") as info:
            await AsyncFileSystemService(transport).watch_dir("/", seen.append, on_exit=exits.append)
        assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")
        # The client closed the connection (the server's read ends).
        assert await asyncio.to_thread(_CLOSED_BY_CLIENT.wait, 10)
    finally:
        await transport.close()
    await asyncio.sleep(0.05)
    assert (exits, seen) == ([], [])


@pytest.mark.anyio
async def test_overflow_events_before_start_count_toward_the_cap_without_an_overflow_callback(
    base: str,
) -> None:
    from daytona.v2.errors import BadRequestError

    _CLOSED_BY_CLIENT.clear()
    transport = AsyncTransport(f"{base}/oflood-4097")
    exits: list[Optional[Exception]] = []
    try:
        with pytest.raises(BadRequestError, match="more than 4096 events before start"):
            await AsyncFileSystemService(transport).watch_dir("/", lambda _event: None, on_exit=exits.append)
        assert await asyncio.to_thread(_CLOSED_BY_CLIENT.wait, 10)
    finally:
        await transport.close()
    await asyncio.sleep(0.05)
    assert exits == []

    transport = AsyncTransport(f"{base}/oflood-4096")
    try:
        handle = await AsyncFileSystemService(transport).watch_dir(
            "/", lambda _event: None, on_exit=exits.append
        )
        assert handle.exit_error is None
        await handle.stop()
    finally:
        await transport.close()
    assert exits == [None]


def test_a_task_a_sync_callback_started_can_stop_its_watch_after_the_callback_returned(base: str) -> None:
    """The task carries the delivery's token: it is still the watch's own, so
    its stop() / wait_for_exit() work on the loop thread instead of raising."""
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.filesystem import FileSystemService

    bridge = SyncBridge()
    try:
        transport = bridge.call(lambda: _transport(f"{base}/burst"))
        box: dict[str, object] = {}
        outcomes: list[object] = []
        done = threading.Event()
        exits: list[Optional[Exception]] = []
        started = threading.Event()

        async def later() -> None:
            await asyncio.sleep(0.05)  # the callback has returned by now
            handle = box["h"]
            try:
                outcomes.append(handle.wait_for_exit())  # type: ignore[attr-defined]
                handle.stop()  # type: ignore[attr-defined]
                outcomes.append("stopped")
            except Exception as error:
                outcomes.append(error)
            done.set()

        def on_event(_event: object) -> None:
            if not started.is_set():
                started.set()
                asyncio.get_running_loop().create_task(later())

        box["h"] = FileSystemService(AsyncFileSystemService(transport), bridge).watch_dir(
            "/", on_event, on_exit=exits.append
        )
        assert done.wait(5)
        deadline = time.monotonic() + 5
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert outcomes == [None, "stopped"]
        assert exits == [None]
        bridge.call(transport.close)
    finally:
        bridge.close()


# Shared with the TypeScript SDK's watch-transport.test.ts: at most 1 MiB of
# event data before start, whatever the count.
@pytest.mark.anyio
async def test_a_watch_holds_at_most_one_mib_of_event_data_before_start(base: str) -> None:
    from daytona.v2.errors import BadRequestError

    seen: list[str] = []
    exits: list[Optional[Exception]] = []
    transport = AsyncTransport(f"{base}/bytes-1048576")
    try:
        handle = await AsyncFileSystemService(transport).watch_dir(
            "/", lambda event: seen.append(event.name[:2])
        )
        for _ in range(500):
            if len(seen) == 2:
                break
            await asyncio.sleep(0.01)
        await handle.stop()
    finally:
        await transport.close()
    assert seen == ["e0", "e1"]

    _CLOSED_BY_CLIENT.clear()
    transport = AsyncTransport(f"{base}/bytes-1048577")
    try:
        with pytest.raises(BadRequestError, match="more than 1048576 bytes of events before start") as info:
            await AsyncFileSystemService(transport).watch_dir("/", lambda _event: None, on_exit=exits.append)
        assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")
        assert await asyncio.to_thread(_CLOSED_BY_CLIENT.wait, 10)
    finally:
        await transport.close()
    await asyncio.sleep(0.05)
    assert exits == []
