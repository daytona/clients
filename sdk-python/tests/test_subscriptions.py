# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Explicit subscriptions: every listened-to id is sent (the server enforces its own per-socket limit)."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona.internal import event_dispatcher as event_dispatcher_module
from daytona.internal import wait_poll
from daytona.internal.event_dispatcher import AsyncEventDispatcher, SyncEventDispatcher
from daytona.internal.event_subscription_manager import AsyncEventSubscriptionManager, SyncEventSubscriptionManager
from daytona_api_client import SandboxState
from daytona_api_client_async import Sandbox as AsyncSandboxDto

from .conftest import make_sandbox_dto
from .test_event_dispatcher import FakeAsyncClient, FakeSyncClient

STATE_EVENT = "sandbox.state.updated"


# Above the gateway's default per-socket limit (10,000): the client must not stop below the server.
MANY_IDS = [f"sb-{i}" for i in range(10_001)]


@pytest.fixture
def async_sio(monkeypatch: pytest.MonkeyPatch) -> type[FakeAsyncClient]:
    FakeAsyncClient.instances = []
    monkeypatch.setattr(event_dispatcher_module.socketio, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


@pytest.fixture
def sync_sio(monkeypatch: pytest.MonkeyPatch) -> type[FakeSyncClient]:
    FakeSyncClient.instances = []
    monkeypatch.setattr(FakeSyncClient, "connect_gate", None)
    monkeypatch.setattr(event_dispatcher_module.socketio, "Client", FakeSyncClient)
    return FakeSyncClient


def _noop(_event: str, _data: Any) -> None:
    pass


def _sent(sio: FakeAsyncClient | FakeSyncClient) -> list[tuple[str, list[str]]]:
    return [(event, data["sandboxIds"]) for event, data in sio.emitted]


def _stopped(resource_id: str) -> dict[str, object]:
    return {"sandbox": {"id": resource_id, "state": "stopped"}, "newState": "stopped"}


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def _async_wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.005)
    assert condition()


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not condition():
        time.sleep(0.005)
    assert condition()


async def _connected_async_dispatcher(monkeypatch: pytest.MonkeyPatch) -> AsyncEventDispatcher:
    dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
    monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
    await dispatcher.connect()
    return dispatcher


def _connected_sync_dispatcher(monkeypatch: pytest.MonkeyPatch) -> SyncEventDispatcher:
    dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
    monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
    dispatcher.connect()
    return dispatcher


def _subscribed_ids(sio: FakeAsyncClient | FakeSyncClient) -> list[str]:
    return [i for event, ids in _sent(sio) if event == "subscribe" for i in ids]


class TestAsyncDispatcherHasNoClientCap:
    async def test_more_ids_than_the_server_default_are_all_sent_on_connect(self, async_sio, monkeypatch):
        dispatcher = AsyncEventDispatcher("https://test.daytona.io/api", "test-token")
        monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
        for resource_id in MANY_IDS:
            _ = dispatcher.subscribe(resource_id, _noop, [STATE_EVENT])

        await dispatcher.connect()
        sio = async_sio.instances[0]

        assert [len(ids) for _, ids in _sent(sio)] == [100] * 100 + [1]
        assert _subscribed_ids(sio) == MANY_IDS
        assert all(dispatcher.is_served(i) for i in MANY_IDS)

        # And a new id while connected, past that count, is sent too.
        _ = dispatcher.subscribe("sb-extra", _noop, [STATE_EVENT])
        await _settle()
        assert _sent(sio)[-1] == ("subscribe", ["sb-extra"])
        assert dispatcher.is_event_backed("sb-extra")
        await dispatcher.disconnect()


class TestSyncDispatcherHasNoClientCap:
    def test_more_ids_than_the_server_default_are_all_sent_on_connect(self, sync_sio, monkeypatch):
        dispatcher = SyncEventDispatcher("https://test.daytona.io/api", "test-token")
        monkeypatch.setattr(dispatcher, "ensure_connected", lambda: None)
        for resource_id in MANY_IDS:
            _ = dispatcher.subscribe(resource_id, _noop, [STATE_EVENT])

        dispatcher.connect()
        sio = sync_sio.instances[0]

        assert [len(ids) for _, ids in _sent(sio)] == [100] * 100 + [1]
        assert _subscribed_ids(sio) == MANY_IDS
        assert all(dispatcher.is_served(i) for i in MANY_IDS)

        _ = dispatcher.subscribe("sb-extra", _noop, [STATE_EVENT])
        assert _sent(sio)[-1] == ("subscribe", ["sb-extra"])
        assert dispatcher.is_event_backed("sb-extra")
        dispatcher.disconnect()


class TestIsEventBacked:
    async def test_async_requires_open_manager_connection_and_served_id(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        manager = AsyncEventSubscriptionManager(dispatcher)
        for resource_id in ("sb-1", "sb-2"):
            _ = manager.subscribe(resource_id, _noop, [STATE_EVENT])

        # sb-3 has no listener, so it is not served.
        assert [manager.is_event_backed(i) for i in ("sb-1", "sb-2", "sb-3")] == [True, True, False]
        await async_sio.instances[0].simulate_disconnect()
        assert not manager.is_event_backed("sb-1")
        await async_sio.instances[0].simulate_connect()
        assert manager.is_event_backed("sb-1")
        manager.shutdown()
        assert not manager.is_event_backed("sb-1")
        assert not AsyncEventSubscriptionManager(None).is_event_backed("sb-1")
        await dispatcher.disconnect()

    def test_sync_requires_open_manager_connection_and_served_id(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        manager = SyncEventSubscriptionManager(dispatcher)
        for resource_id in ("sb-1", "sb-2"):
            _ = manager.subscribe(resource_id, _noop, [STATE_EVENT])

        # sb-3 has no listener, so it is not served.
        assert [manager.is_event_backed(i) for i in ("sb-1", "sb-2", "sb-3")] == [True, True, False]
        sync_sio.instances[0].simulate_disconnect()
        assert not manager.is_event_backed("sb-1")
        sync_sio.instances[0].simulate_connect()
        assert manager.is_event_backed("sb-1")
        manager.shutdown()
        assert not manager.is_event_backed("sb-1")
        assert not SyncEventSubscriptionManager(None).is_event_backed("sb-1")
        dispatcher.disconnect()

    async def test_async_unsubscribe_of_a_released_id_is_a_no_op(self):
        dispatcher = MagicMock()
        unsubscribe_fn = MagicMock()
        dispatcher.subscribe.return_value = unsubscribe_fn
        manager = AsyncEventSubscriptionManager(dispatcher)
        sub_id = manager.subscribe("sb-1", _noop, [STATE_EVENT])

        manager.unsubscribe(sub_id)
        manager.unsubscribe(sub_id)
        manager.unsubscribe("unknown")

        unsubscribe_fn.assert_called_once_with()


class TestAtomicIsEventBacked:
    async def test_async_served_id_on_a_dropped_connection_is_not_event_backed(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        manager = AsyncEventSubscriptionManager(dispatcher)
        _ = manager.subscribe("sb-1", _noop, [STATE_EVENT])
        assert dispatcher.is_event_backed("sb-1") and manager.is_event_backed("sb-1")

        await async_sio.instances[0].simulate_disconnect()

        assert dispatcher.is_served("sb-1")
        assert not dispatcher.is_event_backed("sb-1")
        assert not manager.is_event_backed("sb-1")
        manager.shutdown()
        await dispatcher.disconnect()

    def test_sync_served_id_on_a_dropped_connection_is_not_event_backed(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        manager = SyncEventSubscriptionManager(dispatcher)
        _ = manager.subscribe("sb-1", _noop, [STATE_EVENT])
        assert dispatcher.is_event_backed("sb-1") and manager.is_event_backed("sb-1")

        sync_sio.instances[0].simulate_disconnect()

        assert dispatcher.is_served("sb-1")
        assert not dispatcher.is_event_backed("sb-1")
        assert not manager.is_event_backed("sb-1")
        manager.shutdown()
        dispatcher.disconnect()
