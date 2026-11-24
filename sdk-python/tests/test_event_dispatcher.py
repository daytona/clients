# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import aiohttp
from aiohttp import WSMsgType, web

from daytona.internal import event_dispatcher

AsyncEventDispatcher = event_dispatcher.AsyncEventDispatcher
_TrackedSocketIOClient = event_dispatcher._TrackedSocketIOClient
_WebSocketConnectionTracker = event_dispatcher._WebSocketConnectionTracker

_ENGINEIO_OPEN = (
    '0{"sid":"engineio-test","upgrades":[],"pingInterval":25000,' '"pingTimeout":20000,"maxPayload":1000000}'
)


def _dispatcher() -> AsyncEventDispatcher:
    return AsyncEventDispatcher("https://test.daytona.io/api", "test-token")


async def _start_socketio_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> tuple[web.AppRunner, int]:
    app = web.Application()
    app.router.add_get("/api/socket.io/", handler)
    runner = web.AppRunner(app)
    await runner.setup()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.setblocking(False)
    port = server_socket.getsockname()[1]
    site = web.SockSite(runner, server_socket)
    try:
        await site.start()
    except BaseException:
        server_socket.close()
        await runner.cleanup()
        raise
    return runner, port


def _tracked_client(dispatcher: AsyncEventDispatcher) -> _TrackedSocketIOClient:
    sio = dispatcher._sio
    assert isinstance(sio, _TrackedSocketIOClient)
    return sio


async def _cleanup_real_stack_test(
    dispatcher: AsyncEventDispatcher,
    runner: web.AppRunner,
    release_events: tuple[asyncio.Event, ...] = (),
    disconnect_task: asyncio.Task[None] | None = None,
    websockets: tuple[aiohttp.ClientWebSocketResponse, ...] = (),
) -> list[BaseException]:
    cleanup_errors: list[BaseException] = []
    for event in release_events:
        event.set()

    if disconnect_task is not None:
        try:
            await asyncio.wait_for(disconnect_task, timeout=2)
        except BaseException as error:
            cleanup_errors.append(error)

    sio = dispatcher._sio
    tracked_websocket = sio.websocket_tracker.websocket if isinstance(sio, _TrackedSocketIOClient) else None
    if dispatcher._sio is not None:
        try:
            await dispatcher.disconnect()
        except BaseException as error:
            cleanup_errors.append(error)

    cleanup_websockets = (*websockets, tracked_websocket) if tracked_websocket is not None else websockets
    for websocket in cleanup_websockets:
        if not websocket.closed:
            try:
                await websocket.close()
            except BaseException as error:
                cleanup_errors.append(error)

    try:
        await runner.cleanup()
    except BaseException as error:
        cleanup_errors.append(error)
    return cleanup_errors


def _raise_test_or_cleanup_error(test_error: BaseException | None, cleanup_errors: list[BaseException]) -> None:
    cleanup_error = RuntimeError(f"test cleanup errors: {cleanup_errors!r}") if cleanup_errors else None
    if test_error is not None:
        if cleanup_error is not None:
            raise test_error.with_traceback(test_error.__traceback__) from cleanup_error
        raise test_error.with_traceback(test_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error


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
        websocket_prepared = asyncio.Event()
        allow_open = asyncio.Event()
        publication_wait_started = asyncio.Event()

        async def socketio_handler(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            websocket_prepared.set()
            await allow_open.wait()

            if not websocket.closed:
                await websocket.send_str(_ENGINEIO_OPEN)

            async for message in websocket:
                if message.type is WSMsgType.TEXT and message.data.startswith("40"):
                    await websocket.send_str('40{"sid":"socketio-test"}')
                elif message.type is WSMsgType.TEXT and message.data == "2":
                    await websocket.send_str("3")
            return websocket

        runner, port = await _start_socketio_server(socketio_handler)
        dispatcher = AsyncEventDispatcher(f"http://127.0.0.1:{port}/api", "test-token")
        dispatcher.ensure_connected()
        disconnect_task: asyncio.Task[None] | None = None
        provisional_websocket: aiohttp.ClientWebSocketResponse | None = None

        test_error: BaseException | None = None
        try:
            await asyncio.wait_for(websocket_prepared.wait(), timeout=2)
            sio = _tracked_client(dispatcher)
            tracker = sio.websocket_tracker
            await asyncio.wait_for(tracker.upgraded.wait(), timeout=2)
            websocket = tracker.websocket
            assert websocket is not None
            provisional_websocket = websocket
            connection = websocket._conn
            assert connection is not None
            sock = websocket.get_extra_info("socket")
            assert sock is not None

            original_wait = dispatcher._wait_for_websocket_publication

            async def tracked_wait(connect_task: asyncio.Task[None], tracker_arg: _WebSocketConnectionTracker) -> None:
                publication_wait_started.set()
                await original_wait(connect_task, tracker_arg)

            monkeypatch.setattr(dispatcher, "_wait_for_websocket_publication", tracked_wait)

            disconnect_task = asyncio.create_task(dispatcher.disconnect())
            await asyncio.wait_for(publication_wait_started.wait(), timeout=2)
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

        cleanup_errors = await _cleanup_real_stack_test(
            dispatcher,
            runner,
            (allow_open,),
            disconnect_task,
            (provisional_websocket,) if provisional_websocket is not None else (),
        )
        _raise_test_or_cleanup_error(test_error, cleanup_errors)

    async def test_disconnect_before_websocket_upgrade_returns_promptly(self):
        request_received = asyncio.Event()
        release_request = asyncio.Event()

        async def socketio_handler(_: web.Request) -> web.Response:
            request_received.set()
            await release_request.wait()
            return web.Response(status=503)

        runner, port = await _start_socketio_server(socketio_handler)
        dispatcher = AsyncEventDispatcher(f"http://127.0.0.1:{port}/api", "test-token")
        dispatcher._CLOSE_TIMEOUT = 2
        dispatcher.ensure_connected()

        test_error: BaseException | None = None
        try:
            await asyncio.wait_for(request_received.wait(), timeout=2)
            sio = _tracked_client(dispatcher)
            assert not sio.websocket_tracker.upgraded.is_set()

            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(dispatcher.disconnect(), timeout=0.5)
            elapsed = asyncio.get_running_loop().time() - started

            assert elapsed < 0.5
            assert dispatcher._connect_task is not None
            assert dispatcher._connect_task.done()
        except BaseException as error:
            test_error = error

        cleanup_errors = await _cleanup_real_stack_test(dispatcher, runner, (release_request,))
        _raise_test_or_cleanup_error(test_error, cleanup_errors)

    async def test_disconnect_after_engineio_publishes_websocket_returns_promptly(self):
        namespace_connect_received = asyncio.Event()

        async def socketio_handler(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            await websocket.send_str(_ENGINEIO_OPEN)

            async for message in websocket:
                if message.type is WSMsgType.TEXT and message.data.startswith("40"):
                    namespace_connect_received.set()
                elif message.type is WSMsgType.TEXT and message.data == "2":
                    await websocket.send_str("3")
            return websocket

        runner, port = await _start_socketio_server(socketio_handler)
        dispatcher = AsyncEventDispatcher(f"http://127.0.0.1:{port}/api", "test-token")
        dispatcher._CLOSE_TIMEOUT = 2
        dispatcher.ensure_connected()

        test_error: BaseException | None = None
        try:
            await asyncio.wait_for(namespace_connect_received.wait(), timeout=2)
            sio = _tracked_client(dispatcher)
            assert sio.websocket_tracker.published.is_set()
            assert sio.eio.ws is not None
            assert dispatcher._connect_task is not None
            assert not dispatcher._connect_task.done()

            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(dispatcher.disconnect(), timeout=0.5)
            elapsed = asyncio.get_running_loop().time() - started

            assert elapsed < 0.5
        except BaseException as error:
            test_error = error

        cleanup_errors = await _cleanup_real_stack_test(dispatcher, runner)
        _raise_test_or_cleanup_error(test_error, cleanup_errors)

    async def test_established_connection_cleanup_releases_websocket(self):
        async def socketio_handler(request: web.Request) -> web.WebSocketResponse:
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            await websocket.send_str(_ENGINEIO_OPEN)

            async for message in websocket:
                if message.type is WSMsgType.TEXT and message.data.startswith("40"):
                    await websocket.send_str('40{"sid":"socketio-test"}')
                elif message.type is WSMsgType.TEXT and message.data == "2":
                    await websocket.send_str("3")
            return websocket

        runner, port = await _start_socketio_server(socketio_handler)
        dispatcher = AsyncEventDispatcher(f"http://127.0.0.1:{port}/api", "test-token")
        dispatcher.ensure_connected()
        established_websocket: aiohttp.ClientWebSocketResponse | None = None

        test_error: BaseException | None = None
        try:
            connect_task = dispatcher._connect_task
            assert connect_task is not None
            await asyncio.wait_for(connect_task, timeout=2)
            assert dispatcher.is_connected
            sio = _tracked_client(dispatcher)
            websocket = sio.websocket_tracker.websocket
            assert websocket is not None
            established_websocket = websocket
            connection = websocket._conn
            assert connection is not None
            sock = websocket.get_extra_info("socket")
            assert sock is not None

            await asyncio.wait_for(dispatcher.disconnect(), timeout=2)

            assert websocket.closed
            assert connection._protocol is None
            assert sock.fileno() == -1
        except BaseException as error:
            test_error = error

        cleanup_errors = await _cleanup_real_stack_test(
            dispatcher,
            runner,
            websockets=(established_websocket,) if established_websocket is not None else (),
        )
        _raise_test_or_cleanup_error(test_error, cleanup_errors)

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
