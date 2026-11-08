# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Directory watches over a real HTTP transport: a close-delimited SSE body (no
Content-Length, the connection closes when the stream ends) and one that ends
short of its Content-Length."""

from __future__ import annotations

import asyncio
import json
import threading
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

    base = "http://127.0.0.1:9" if case == "refused" else f"{setup_base}/{case}"
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
async def test_cancelling_watch_dir_during_the_handshake_calls_no_exit_callback() -> None:
    from daytona.v2.filesystem import AsyncFileSystemService as Service

    class Hold:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def iter_lines(self):
            await self.release.wait()
            return
            yield  # pragma: no cover

        async def close(self) -> None:
            self.release.set()

    class Transport:
        async def open_stream(self, spec: RequestSpec) -> Hold:
            return Hold()

    exits: list[Optional[Exception]] = []
    task = asyncio.ensure_future(Service(Transport()).watch_dir("/", lambda _e: None, on_exit=exits.append))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert exits == []
