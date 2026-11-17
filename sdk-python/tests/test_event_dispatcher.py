# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest

from daytona.internal import event_dispatcher as event_dispatcher_module
from daytona.internal.event_dispatcher import AsyncEventDispatcher, SyncEventDispatcher


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


SLIM_STATE_EVENT = {
    "sandbox": {"id": "sb-1", "state": "started", "desiredState": "started"},
    "oldState": "starting",
    "newState": "started",
}


class FakeAsyncClient:
    """socketio.AsyncClient stand-in that records the handshake URL and emitted messages."""

    instances: list[FakeAsyncClient] = []
    after_emit: Callable[[], None] | None = None

    def __init__(self, **_kwargs: object) -> None:
        self.handlers: dict[str, Any] = {}
        self.connect_url: str | None = None
        self.connected = False
        self.emitted: list[tuple[str, Any]] = []
        self.fail_emit = False
        FakeAsyncClient.instances.append(self)

    def event(self, handler: Any) -> Any:
        self.handlers[handler.__name__] = handler
        return handler

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    async def connect(self, url: str, **_kwargs: object) -> None:
        self.connect_url = url
        await self.simulate_connect()

    async def simulate_connect(self) -> None:
        self.connected = True
        await self.handlers["connect"]()

    async def simulate_disconnect(self) -> None:
        self.connected = False
        await self.handlers["disconnect"]()

    async def emit(self, event: str, data: Any) -> None:
        if self.fail_emit or not self.connected:
            raise RuntimeError("/ is not a connected namespace.")
        self.emitted.append((event, data))
        if self.after_emit is not None:
            self.after_emit()
        await asyncio.sleep(0)  # like a real send, give other tasks a turn

    async def disconnect(self) -> None:
        if self.connected:
            await self.simulate_disconnect()


class FakeSyncClient:
    """socketio.Client stand-in that records the handshake URL and emitted messages."""

    instances: list[FakeSyncClient] = []

    def __init__(self, **_kwargs: object) -> None:
        self.handlers: dict[str, Any] = {}
        self.connect_url: str | None = None
        self.connected = False
        self.emitted: list[tuple[str, Any]] = []
        self.fail_emit = False
        FakeSyncClient.instances.append(self)

    def event(self, handler: Any) -> Any:
        self.handlers[handler.__name__] = handler
        return handler

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def connect(self, url: str, **_kwargs: object) -> None:
        self.connect_url = url
        self.simulate_connect()

    def simulate_connect(self) -> None:
        self.connected = True
        self.handlers["connect"]()

    def simulate_disconnect(self) -> None:
        self.connected = False
        self.handlers["disconnect"]()

    def emit(self, event: str, data: Any) -> None:
        if self.fail_emit or not self.connected:
            raise RuntimeError("/ is not a connected namespace.")
        self.emitted.append((event, data))

    def disconnect(self) -> None:
        if self.connected:
            self.simulate_disconnect()


def _noop(_event: str, _data: Any) -> None:
    pass


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture
def fake_async_sio(monkeypatch: pytest.MonkeyPatch) -> type[FakeAsyncClient]:
    FakeAsyncClient.instances = []
    monkeypatch.setattr(event_dispatcher_module.socketio, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


@pytest.fixture
def fake_sync_sio(monkeypatch: pytest.MonkeyPatch) -> type[FakeSyncClient]:
    FakeSyncClient.instances = []
    monkeypatch.setattr(event_dispatcher_module.socketio, "Client", FakeSyncClient)
    return FakeSyncClient


def _subscribe_messages(sio: FakeAsyncClient | FakeSyncClient) -> list[list[str]]:
    return [data["sandboxIds"] for event, data in sio.emitted if event == "subscribe"]


class TestAsyncEventDispatcherExplicitSubscriptions:
    async def test_construction_does_not_connect(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")

        await _settle()

        assert dispatcher._connect_task is None
        assert not fake_async_sio.instances

    async def test_first_subscription_connects_in_background(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")

        _ = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        assert not dispatcher.is_connected  # does not block the caller
        assert dispatcher._connect_task is not None
        await dispatcher._connect_task

        assert dispatcher.is_connected
        assert _subscribe_messages(fake_async_sio.instances[0]) == [["sb-1"]]
        await dispatcher.disconnect()

    async def test_handshake_opts_into_explicit_subscriptions(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher(
            "https://test.daytona.io/api", "test-token", "org-1", "sdk-python-async", "1.2.3"
        )

        await dispatcher.connect()

        url = fake_async_sio.instances[0].connect_url
        assert url is not None
        query = parse_qs(urlparse(url).query)
        assert query["subscriptionMode"] == ["explicit"]
        assert query["organizationId"] == ["org-1"]
        assert query["source"] == ["sdk-python-async"]
        assert query["sdkVersion"] == ["1.2.3"]
        await dispatcher.disconnect()

    async def test_connect_and_reconnect_send_the_full_set_in_chunks(self, fake_async_sio, monkeypatch):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
        ids = [f"sb-{i}" for i in range(250)]
        for resource_id in ids:
            _ = dispatcher.subscribe(resource_id, _noop, ["sandbox.state.updated"])

        await dispatcher.connect()
        sio = fake_async_sio.instances[0]

        chunks = _subscribe_messages(sio)
        assert [len(chunk) for chunk in chunks] == [100, 100, 50]
        assert [i for chunk in chunks for i in chunk] == ids

        # python-socketio reconnects with the same client object and fires its connect handler again.
        await sio.simulate_disconnect()
        sio.emitted.clear()
        await sio.simulate_connect()

        assert len(fake_async_sio.instances) == 1
        assert [i for chunk in _subscribe_messages(sio) for i in chunk] == ids
        await dispatcher.disconnect()

    async def test_unsubscribe_during_the_full_set_is_sent_after_it(self, fake_async_sio, monkeypatch):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
        unsubscribes = {
            f"sb-{i}": dispatcher.subscribe(f"sb-{i}", _noop, ["sandbox.state.updated"]) for i in range(250)
        }
        unsubscribed = False

        def unsubscribe_after_first_chunk() -> None:
            nonlocal unsubscribed
            if not unsubscribed:
                unsubscribed = True
                unsubscribes["sb-240"]()  # sb-240 is in the third chunk

        monkeypatch.setattr(fake_async_sio, "after_emit", staticmethod(unsubscribe_after_first_chunk))

        await dispatcher.connect()
        await _settle()

        sio = fake_async_sio.instances[0]
        events = [(event, data["sandboxIds"]) for event, data in sio.emitted]
        assert [event for event, _ in events] == ["subscribe", "subscribe", "subscribe", "unsubscribe"]
        assert "sb-240" in events[2][1]
        assert events[3] == ("unsubscribe", ["sb-240"])
        await dispatcher.disconnect()

    async def test_first_and_last_listener_emit_while_connected(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        await dispatcher.connect()
        sio = fake_async_sio.instances[0]

        unsub_a = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        unsub_b = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        await _settle()
        assert sio.emitted == [("subscribe", {"sandboxIds": ["sb-1"]})]

        unsub_a()
        await _settle()
        assert len(sio.emitted) == 1

        unsub_b()
        await _settle()
        assert sio.emitted[1:] == [("unsubscribe", {"sandboxIds": ["sb-1"]})]
        await dispatcher.disconnect()

    async def test_nothing_is_sent_while_disconnected(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        await dispatcher.connect()
        sio = fake_async_sio.instances[0]
        await sio.simulate_disconnect()

        unsub = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        unsub()
        unsub_2 = dispatcher.subscribe("sb-2", _noop, ["sandbox.state.updated"])
        await _settle()
        assert sio.emitted == []

        await sio.simulate_connect()
        assert sio.emitted == [("subscribe", {"sandboxIds": ["sb-2"]})]
        unsub_2()
        await dispatcher.disconnect()

    async def test_emit_failure_does_not_reach_the_caller(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        await dispatcher.connect()
        fake_async_sio.instances[0].fail_emit = True

        unsub = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        unsub()
        await _settle()

        assert not dispatcher._emit_tasks
        await dispatcher.disconnect()

    async def test_slim_state_payload_is_routed_by_sandbox_id(self, fake_async_sio):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        await dispatcher.connect()
        received: list[tuple[str, Any]] = []
        other: list[Any] = []
        _ = dispatcher.subscribe("sb-1", lambda event, data: received.append((event, data)), ["sandbox.state.updated"])
        _ = dispatcher.subscribe("sb-2", lambda _event, data: other.append(data), ["sandbox.state.updated"])

        await fake_async_sio.instances[0].handlers["sandbox.state.updated"](SLIM_STATE_EVENT)

        assert received == [("sandbox.state.updated", SLIM_STATE_EVENT)]
        assert other == []
        await dispatcher.disconnect()


class TestSyncEventDispatcherExplicitSubscriptions:
    def test_construction_does_not_connect(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")

        assert dispatcher._bg_thread is None
        assert not fake_sync_sio.instances

    def test_first_subscription_connects_in_background(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")

        _ = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        assert dispatcher._bg_thread is not None
        dispatcher._bg_thread.join(timeout=5)

        assert dispatcher.is_connected
        assert _subscribe_messages(fake_sync_sio.instances[0]) == [["sb-1"]]
        dispatcher.disconnect()

    def test_handshake_opts_into_explicit_subscriptions(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token", "org-1", "sdk-python", "1.2.3")

        dispatcher.connect()

        url = fake_sync_sio.instances[0].connect_url
        assert url is not None
        query = parse_qs(urlparse(url).query)
        assert query["subscriptionMode"] == ["explicit"]
        assert query["organizationId"] == ["org-1"]
        assert query["source"] == ["sdk-python"]
        assert query["sdkVersion"] == ["1.2.3"]
        dispatcher.disconnect()

    def test_connect_and_reconnect_send_the_full_set_in_chunks(self, fake_sync_sio, monkeypatch):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
        monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
        ids = [f"sb-{i}" for i in range(201)]
        for resource_id in ids:
            _ = dispatcher.subscribe(resource_id, _noop, ["sandbox.state.updated"])

        dispatcher.connect()
        sio = fake_sync_sio.instances[0]

        chunks = _subscribe_messages(sio)
        assert [len(chunk) for chunk in chunks] == [100, 100, 1]
        assert [i for chunk in chunks for i in chunk] == ids

        # python-socketio reconnects with the same client object and fires its connect handler again.
        sio.simulate_disconnect()
        sio.emitted.clear()
        sio.simulate_connect()

        assert len(fake_sync_sio.instances) == 1
        assert [i for chunk in _subscribe_messages(sio) for i in chunk] == ids
        dispatcher.disconnect()

    def test_first_and_last_listener_emit_while_connected(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
        dispatcher.connect()
        sio = fake_sync_sio.instances[0]

        unsub_a = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        unsub_b = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        assert sio.emitted == [("subscribe", {"sandboxIds": ["sb-1"]})]

        unsub_a()
        assert len(sio.emitted) == 1

        unsub_b()
        assert sio.emitted[1:] == [("unsubscribe", {"sandboxIds": ["sb-1"]})]
        dispatcher.disconnect()

    def test_nothing_is_sent_while_disconnected(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
        dispatcher.connect()
        sio = fake_sync_sio.instances[0]
        sio.simulate_disconnect()

        unsub = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        unsub()
        unsub_2 = dispatcher.subscribe("sb-2", _noop, ["sandbox.state.updated"])
        assert sio.emitted == []

        sio.simulate_connect()
        assert sio.emitted == [("subscribe", {"sandboxIds": ["sb-2"]})]
        unsub_2()
        dispatcher.disconnect()

    def test_emit_failure_does_not_reach_the_caller(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
        dispatcher.connect()
        fake_sync_sio.instances[0].fail_emit = True

        unsub = dispatcher.subscribe("sb-1", _noop, ["sandbox.state.updated"])
        unsub()

        assert dispatcher._listeners == {}
        dispatcher.disconnect()

    def test_slim_state_payload_is_routed_by_sandbox_id(self, fake_sync_sio):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
        dispatcher.connect()
        received: list[tuple[str, Any]] = []
        other: list[Any] = []
        _ = dispatcher.subscribe("sb-1", lambda event, data: received.append((event, data)), ["sandbox.state.updated"])
        _ = dispatcher.subscribe("sb-2", lambda _event, data: other.append(data), ["sandbox.state.updated"])

        fake_sync_sio.instances[0].handlers["sandbox.state.updated"](SLIM_STATE_EVENT)

        assert received == [("sandbox.state.updated", SLIM_STATE_EVENT)]
        assert other == []
        dispatcher.disconnect()
