# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import aiohttp
import httpx
import pytest
from aiohttp import web

from daytona._utils.stream import _std_demux_loop
from daytona.common.process import STDERR_PREFIX, STDOUT_PREFIX

# Each case is the list of WebSocket messages the daemon sends. The daemon forwards
# log-file reads as-is, so message boundaries can land anywhere, including right
# after a complete stream marker.
DEMUX_CASES = {
    "single message": (
        [STDOUT_PREFIX + b"out\n" + STDERR_PREFIX + b"err\n"],
        ("out\n", "err\n"),
    ),
    "marker split 2+1": (
        [STDOUT_PREFIX + b"out\n" + STDERR_PREFIX[:2], STDERR_PREFIX[2:] + b"err\n"],
        ("out\n", "err\n"),
    ),
    "marker split 1+2": (
        [STDOUT_PREFIX + b"out\n" + STDERR_PREFIX[:1], STDERR_PREFIX[1:] + b"err\n"],
        ("out\n", "err\n"),
    ),
    "message is only a marker": (
        [STDOUT_PREFIX, b"out\n"],
        ("out\n", ""),
    ),
    "message ends with a stderr marker": (
        [STDOUT_PREFIX + b"out\n" + STDERR_PREFIX, b"err\n"],
        ("out\n", "err\n"),
    ),
    "message ends with a stdout marker": (
        [STDOUT_PREFIX + b"a\n" + STDOUT_PREFIX, b"b\n"],
        ("a\nb\n", ""),
    ),
}

# Every message boundary lands right after a marker.
MARKER_ALIGNED_FRAMES = [STDOUT_PREFIX, b"out\n", STDERR_PREFIX, b"err\n"]


class TestStdDemuxLoop:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("frames,expected", DEMUX_CASES.values(), ids=DEMUX_CASES.keys())
    async def test_demultiplexes_regardless_of_message_boundaries(self, frames, expected):
        messages = iter(frames)

        async def recv():
            return next(messages, None)

        stdout: list[str] = []
        stderr: list[str] = []
        await _std_demux_loop(recv, stdout.append, stderr.append)

        assert ("".join(stdout), "".join(stderr)) == expected


class _LogWebSocketServer:
    """Local WebSocket server that sends fixed binary messages, then closes normally.

    Runs on its own thread and loop so the sync SDK's blocking httpx_ws handshake
    can reach it from inside a test's event loop.
    """

    def __init__(self, frames: list[bytes]):
        self._frames = frames
        self._loop = asyncio.new_event_loop()
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.port = 0

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for frame in self._frames:
            await ws.send_bytes(frame)
        await ws.close()
        return ws

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        self._loop.run_until_complete(self._runner.setup())
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        self._loop.run_until_complete(site.start())
        self.port = self._runner.addresses[0][1]
        self._ready.set()
        self._loop.run_forever()

    def logs_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/process/session/s1/command/c1/logs"

    def __enter__(self) -> "_LogWebSocketServer":
        self._thread.start()
        assert self._ready.wait(5), "log server did not start"
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self._runner is not None
        asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop.close()


def _api_client_for(url: str) -> MagicMock:
    api_client = MagicMock()
    api_client._get_session_command_logs_serialize.return_value = ("GET", url, {}, None)
    return api_client


class TestSessionCommandLogsStreaming:
    @pytest.mark.asyncio
    async def test_async_process_streams_marker_aligned_messages(self):
        from daytona._async.process import AsyncProcess

        stdout: list[str] = []
        stderr: list[str] = []
        with _LogWebSocketServer(MARKER_ALIGNED_FRAMES) as server:
            api_client = _api_client_for(server.logs_url())
            async with aiohttp.ClientSession() as session:
                api_client.api_client.http_session = session
                process = AsyncProcess("python", api_client)

                await process.get_session_command_logs_async("s1", "c1", stdout.append, stderr.append)

        assert "".join(stdout) == "out\n"
        assert "".join(stderr) == "err\n"

    @pytest.mark.asyncio
    async def test_sync_process_streams_marker_aligned_messages(self):
        from daytona._sync.process import Process

        stdout: list[str] = []
        stderr: list[str] = []
        with _LogWebSocketServer(MARKER_ALIGNED_FRAMES) as server, httpx.Client() as http_client:
            process = Process("python", _api_client_for(server.logs_url()), http_client=http_client)

            await process.get_session_command_logs_async("s1", "c1", stdout.append, stderr.append)

        assert "".join(stdout) == "out\n"
        assert "".join(stderr) == "err\n"
