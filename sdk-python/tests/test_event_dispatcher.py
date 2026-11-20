# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiohttp
from aiohttp import WSMsgType, web

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

    async def test_disconnect_during_engineio_open_wait_releases_provisional_websocket(self, monkeypatch):
        """Regression: disconnecting after the WebSocket upgrade but before Engine.IO's OPEN packet
        must close the provisional aiohttp WebSocket that Engine.IO has not assigned to ``eio.ws`` yet.
        """
        websocket_created = asyncio.Event()
        allow_open = asyncio.Event()
        disconnect_entered = asyncio.Event()
        provisional_websockets: list[aiohttp.ClientWebSocketResponse] = []

        original_ws_connect = aiohttp.ClientSession.ws_connect

        async def tracked_ws_connect(
            session: aiohttp.ClientSession, *args: object, **kwargs: object
        ) -> aiohttp.ClientWebSocketResponse:
            websocket = await original_ws_connect(session, *args, **kwargs)
            provisional_websockets.append(websocket)
            websocket_created.set()
            return websocket

        monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", tracked_ws_connect)

        async def socketio_handler(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            await allow_open.wait()

            if not websocket.closed:
                await websocket.send_str(
                    '0{"sid":"engineio-test","upgrades":[],"pingInterval":25000,'
                    '"pingTimeout":20000,"maxPayload":1000000}'
                )

            async for message in websocket:
                if message.type is WSMsgType.TEXT and message.data.startswith("40"):
                    await websocket.send_str('40{"sid":"socketio-test"}')
                elif message.type is WSMsgType.TEXT and message.data == "2":
                    await websocket.send_str("3")
            return websocket

        app = web.Application()
        app.router.add_get("/api/socket.io/", socketio_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        assert site._server is not None
        sockets = site._server.sockets
        assert sockets is not None
        port = sockets[0].getsockname()[1]

        dispatcher = AsyncEventDispatcher(f"http://127.0.0.1:{port}/api", "test-token")
        dispatcher.ensure_connected()
        disconnect_task: asyncio.Task[None] | None = None
        original_disconnect = dispatcher._disconnect

        async def tracked_disconnect(expected_generation: int | None = None) -> None:
            disconnect_entered.set()
            await original_disconnect(expected_generation)

        monkeypatch.setattr(dispatcher, "_disconnect", tracked_disconnect)

        test_error: BaseException | None = None
        try:
            await asyncio.wait_for(websocket_created.wait(), timeout=2)
            assert len(provisional_websockets) == 1
            websocket = provisional_websockets[0]
            connection = websocket._conn
            assert connection is not None
            sock = websocket.get_extra_info("socket")
            assert sock is not None
            sio = dispatcher._sio
            assert sio is not None

            disconnect_task = asyncio.create_task(dispatcher.disconnect())
            await asyncio.wait_for(disconnect_entered.wait(), timeout=2)
            assert sio.eio.ws is None
            assert not allow_open.is_set()
            allow_open.set()
            await asyncio.wait_for(disconnect_task, timeout=2)

            connection_released = connection._protocol is None
            socket_closed = sock.fileno() == -1
            assert websocket.closed and connection_released and socket_closed, (
                "provisional WebSocket resources were not fully released: "
                f"websocket.closed={websocket.closed}, "
                f"connection_released={connection_released}, "
                f"socket_closed={socket_closed}"
            )
        except BaseException as error:
            test_error = error

        cleanup_errors: list[BaseException] = []
        allow_open.set()
        try:
            if disconnect_task is not None and not disconnect_task.done():
                await asyncio.wait_for(disconnect_task, timeout=2)
            elif not dispatcher._closed:
                await dispatcher.disconnect()
        except BaseException as error:
            cleanup_errors.append(error)

        for websocket in provisional_websockets:
            try:
                if not websocket.closed:
                    await websocket.close()
            except BaseException as error:
                cleanup_errors.append(error)

        try:
            await runner.cleanup()
        except BaseException as error:
            cleanup_errors.append(error)

        cleanup_error = RuntimeError(f"test cleanup errors: {cleanup_errors!r}") if cleanup_errors else None
        if test_error is not None:
            if cleanup_error is not None:
                raise test_error.with_traceback(test_error.__traceback__) from cleanup_error
            raise test_error.with_traceback(test_error.__traceback__)
        if cleanup_error is not None:
            raise cleanup_error

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
