# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from daytona.internal.event_dispatcher import AsyncEventDispatcher


def _dispatcher() -> AsyncEventDispatcher:
    return AsyncEventDispatcher("https://test.daytona.io/api", "test-token")


class FakeSocketIO:
    """socketio.AsyncClient stand-in whose websocket, like aiohttp's, closes only after disconnect() returns."""

    def __init__(self) -> None:
        self.connection_lost: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        protocol = SimpleNamespace(closed=self.connection_lost)
        self.eio = SimpleNamespace(ws=SimpleNamespace(_conn=SimpleNamespace(protocol=protocol)))
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True
        _ = asyncio.get_running_loop().call_later(0.05, self.connection_lost.set_result, None)


class TestAsyncEventDispatcherDisconnect:
    async def test_waits_for_the_cancelled_connect_task_to_clean_up(self):
        """Regression: disconnect() cancelled the connect task without awaiting it, so on a loop that
        stops right after close() (e.g. one driven by loop.run_until_complete) the cancelled task was
        left pending and its cleanup never ran.
        """
        dispatcher = _dispatcher()
        started = asyncio.Event()
        cleaned_up = False

        async def connect() -> None:
            nonlocal cleaned_up
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                await asyncio.sleep(0)  # cleanup that needs another turn of the loop
                cleaned_up = True

        dispatcher._connect_task = asyncio.get_running_loop().create_task(connect())
        await started.wait()

        await dispatcher.disconnect()

        assert cleaned_up
        assert dispatcher._connect_task.done()

    async def test_closes_socketio_before_waiting_for_the_cancelled_connect_task(self):
        """Regression: waiting for the cancelled connect task before sio.disconnect() let a task cancelled
        mid-upgrade release its connection from engine.io's aiohttp session first, so the session close
        inside sio.disconnect() no longer waited for that socket.
        """
        dispatcher = _dispatcher()
        sio = FakeSocketIO()
        dispatcher._sio = sio
        started = asyncio.Event()
        order: list[str] = []

        async def connect() -> None:
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                order.append("connect task cleaned up")

        dispatcher._connect_task = asyncio.get_running_loop().create_task(connect())
        await started.wait()
        fake_disconnect = sio.disconnect

        async def disconnect() -> None:
            order.append("socketio disconnected")
            await fake_disconnect()

        sio.disconnect = disconnect

        await dispatcher.disconnect()

        assert order == ["socketio disconnected", "connect task cleaned up"]

    async def test_waits_for_the_websocket_connection_to_close(self):
        """Regression: aiohttp closes the websocket's transport after sio.disconnect() returns, so
        close() returned with the event socket still open and a loop stopped right after leaked it.
        """
        dispatcher = _dispatcher()
        sio = FakeSocketIO()
        dispatcher._sio = sio

        await dispatcher.disconnect()

        assert sio.disconnected
        assert sio.connection_lost.done()

    async def test_is_bounded_when_a_task_ignores_cancellation(self):
        dispatcher = _dispatcher()
        dispatcher._CLOSE_TIMEOUT = 0.1
        release = asyncio.Event()

        async def stuck() -> None:
            while not release.is_set():
                try:
                    _ = await release.wait()
                except asyncio.CancelledError:
                    pass

        task = asyncio.get_running_loop().create_task(stuck())
        dispatcher._connect_task = task
        await asyncio.sleep(0)

        await asyncio.wait_for(dispatcher.disconnect(), timeout=2)

        assert not task.done()
        release.set()
        await task

    async def test_delayed_disconnect_does_not_wait_on_itself(self):
        dispatcher = _dispatcher()
        dispatcher._DISCONNECT_DELAY = 0
        sio = FakeSocketIO()
        dispatcher._sio = sio
        dispatcher._schedule_delayed_disconnect()
        task = dispatcher._disconnect_task
        assert task is not None

        await asyncio.wait_for(task, timeout=2)

        assert not task.cancelled()
        assert sio.disconnected
        assert dispatcher._sio is None
