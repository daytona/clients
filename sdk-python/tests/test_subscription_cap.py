# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The per-connection subscription cap: ids past it are not sent, and their waits poll."""

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


@pytest.fixture(autouse=True)
def _cap_of_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_dispatcher_module, "MAX_SUBSCRIPTIONS_PER_CONNECTION", 2)
    monkeypatch.setattr(wait_poll, "random_unit", lambda: 0.5)


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


class TestAsyncDispatcherCap:
    async def test_ids_past_the_cap_are_not_sent(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        for resource_id in ("sb-1", "sb-2", "sb-3"):
            _ = dispatcher.subscribe(resource_id, _noop, [STATE_EVENT])
        _ = dispatcher.subscribe("sb-1", _noop, [STATE_EVENT])  # a second listener takes no slot
        await _settle()

        assert _sent(async_sio.instances[0]) == [("subscribe", ["sb-1"]), ("subscribe", ["sb-2"])]
        assert [dispatcher.is_served(i) for i in ("sb-1", "sb-2", "sb-3")] == [True, True, False]
        await dispatcher.disconnect()

    async def test_a_freed_slot_goes_to_the_next_new_id(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        unsub = {i: dispatcher.subscribe(i, _noop, [STATE_EVENT]) for i in ("sb-1", "sb-2", "sb-3")}
        await _settle()
        sio = async_sio.instances[0]
        sio.emitted.clear()

        unsub["sb-1"]()
        unsub["sb-4"] = dispatcher.subscribe("sb-4", _noop, [STATE_EVENT])
        await _settle()

        assert _sent(sio) == [("unsubscribe", ["sb-1"]), ("subscribe", ["sb-4"])]
        assert not dispatcher.is_served("sb-3")  # no promotion
        assert dispatcher.is_served("sb-4")

        sio.emitted.clear()
        unsub["sb-3"]()  # an unserved id leaves without a message
        await _settle()
        assert sio.emitted == []
        await dispatcher.disconnect()

    async def test_reconnect_resends_exactly_the_served_ids(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        for resource_id in ("sb-1", "sb-2", "sb-3"):
            _ = dispatcher.subscribe(resource_id, _noop, [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]

        await sio.simulate_disconnect()
        sio.emitted.clear()
        await sio.simulate_connect()

        assert _sent(sio) == [("subscribe", ["sb-1", "sb-2"])]
        await dispatcher.disconnect()


class TestSyncDispatcherCap:
    def test_ids_past_the_cap_are_not_sent(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        for resource_id in ("sb-1", "sb-2", "sb-3"):
            _ = dispatcher.subscribe(resource_id, _noop, [STATE_EVENT])
        _ = dispatcher.subscribe("sb-1", _noop, [STATE_EVENT])  # a second listener takes no slot

        assert _sent(sync_sio.instances[0]) == [("subscribe", ["sb-1"]), ("subscribe", ["sb-2"])]
        assert [dispatcher.is_served(i) for i in ("sb-1", "sb-2", "sb-3")] == [True, True, False]
        dispatcher.disconnect()

    def test_a_freed_slot_goes_to_the_next_new_id(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        unsub = {i: dispatcher.subscribe(i, _noop, [STATE_EVENT]) for i in ("sb-1", "sb-2", "sb-3")}
        sio = sync_sio.instances[0]
        sio.emitted.clear()

        unsub["sb-1"]()
        unsub["sb-4"] = dispatcher.subscribe("sb-4", _noop, [STATE_EVENT])

        assert _sent(sio) == [("unsubscribe", ["sb-1"]), ("subscribe", ["sb-4"])]
        assert not dispatcher.is_served("sb-3")  # no promotion
        assert dispatcher.is_served("sb-4")

        sio.emitted.clear()
        unsub["sb-3"]()  # an unserved id leaves without a message
        assert sio.emitted == []
        dispatcher.disconnect()

    def test_reconnect_resends_exactly_the_served_ids(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        for resource_id in ("sb-1", "sb-2", "sb-3"):
            _ = dispatcher.subscribe(resource_id, _noop, [STATE_EVENT])
        sio = sync_sio.instances[0]

        sio.simulate_disconnect()
        sio.emitted.clear()
        sio.simulate_connect()

        assert _sent(sio) == [("subscribe", ["sb-1", "sb-2"])]
        dispatcher.disconnect()


class TestIsEventBacked:
    async def test_async_requires_open_manager_connection_and_served_id(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        manager = AsyncEventSubscriptionManager(dispatcher)
        for resource_id in ("sb-1", "sb-2", "sb-3"):
            _ = manager.subscribe(resource_id, _noop, [STATE_EVENT])

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
        for resource_id in ("sb-1", "sb-2", "sb-3"):
            _ = manager.subscribe(resource_id, _noop, [STATE_EVENT])

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


class TestAsyncSandboxWaitsPastTheCap:
    async def test_overflow_wait_polls_and_a_freed_slot_serves_the_next_wait(
        self, async_sio, monkeypatch, mock_async_toolbox_api_client
    ):
        from daytona._async import sandbox as sandbox_module

        polls: dict[str, list[float]] = {}
        real_wait_for = asyncio.wait_for

        class RecordingAsyncio:
            """The sandbox module's ``asyncio``: records each wait's poll interval, then waits for its event."""

            def __getattr__(self, name: str):
                return getattr(asyncio, name)

            @staticmethod
            async def wait_for(awaitable, timeout):
                task = asyncio.current_task()
                assert task is not None
                polls.setdefault(task.get_name(), []).append(timeout)
                return await real_wait_for(awaitable, timeout=None)

        monkeypatch.setattr(sandbox_module, "asyncio", RecordingAsyncio())
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        manager = AsyncEventSubscriptionManager(dispatcher)
        sio = async_sio.instances[0]
        sandbox_api = AsyncMock()

        def dto(sandbox_id: str, *_args: object, **_kwargs: object) -> AsyncSandboxDto:
            return AsyncSandboxDto(**make_sandbox_dto(sandbox_id=sandbox_id, state=SandboxState.STOPPING).model_dump())

        sandbox_api.stop_sandbox = AsyncMock(side_effect=dto)
        sandbox_api.get_sandbox = AsyncMock(side_effect=dto)
        tasks: dict[str, asyncio.Task[None]] = {}

        async def start_stop(sandbox_id: str) -> None:
            sandbox = sandbox_module.AsyncSandbox(
                make_sandbox_dto(sandbox_id=sandbox_id),
                mock_async_toolbox_api_client,
                sandbox_api,
                "python",
                subscription_manager=manager,
            )
            tasks[sandbox_id] = asyncio.create_task(sandbox.stop(timeout=0), name=sandbox_id)
            await _async_wait_until(lambda: sandbox_id in polls)
            await _settle()

        for sandbox_id in ("sb-1", "sb-2", "sb-3"):
            await start_stop(sandbox_id)

        assert _sent(sio) == [("subscribe", ["sb-1"]), ("subscribe", ["sb-2"])]
        # The overflow wait reads first and polls at the subscribed rate; the others skip and wait 5 s.
        assert [c.args[0] for c in sandbox_api.get_sandbox.await_args_list] == ["sb-3"]
        assert polls == {"sb-1": [pytest.approx(5.0)], "sb-2": [pytest.approx(5.0)], "sb-3": [pytest.approx(1.0)]}

        await dispatcher._dispatch("sb-1", STATE_EVENT, _stopped("sb-1"))
        await real_wait_for(tasks["sb-1"], timeout=2)
        await _settle()
        assert _sent(sio)[-1] == ("unsubscribe", ["sb-1"])
        assert not dispatcher.is_served("sb-3")

        await start_stop("sb-4")
        assert _sent(sio)[-1] == ("subscribe", ["sb-4"])
        assert polls["sb-4"] == [pytest.approx(5.0)]
        assert [c.args[0] for c in sandbox_api.get_sandbox.await_args_list] == ["sb-3"]

        for sandbox_id in ("sb-2", "sb-3", "sb-4"):
            await dispatcher._dispatch(sandbox_id, STATE_EVENT, _stopped(sandbox_id))
        _ = await real_wait_for(asyncio.gather(*tasks.values()), timeout=2)
        manager.shutdown()
        await dispatcher.disconnect()


class TestSyncSandboxWaitsPastTheCap:
    def test_overflow_wait_polls_and_a_freed_slot_serves_the_next_wait(
        self, sync_sio, monkeypatch, mock_toolbox_api_client
    ):
        from daytona._sync import sandbox as sandbox_module

        polls: dict[str, list[float]] = {}

        class RecordingEvent(threading.Event):
            def wait(self, timeout: float | None = None) -> bool:
                if timeout is not None:
                    polls.setdefault(threading.current_thread().name, []).append(timeout)
                return super().wait(5)

        class RecordingThreading:
            """The sandbox module's ``threading``: records each wait's poll interval, then waits for its event."""

            Event = RecordingEvent

            def __getattr__(self, name: str):
                return getattr(threading, name)

        monkeypatch.setattr(sandbox_module, "threading", RecordingThreading())
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        manager = SyncEventSubscriptionManager(dispatcher)
        sio = sync_sio.instances[0]
        sandbox_api = MagicMock()

        def dto(sandbox_id: str, *_args: object, **_kwargs: object):
            return make_sandbox_dto(sandbox_id=sandbox_id, state=SandboxState.STOPPING)

        sandbox_api.stop_sandbox.side_effect = dto
        sandbox_api.get_sandbox.side_effect = dto
        threads: dict[str, threading.Thread] = {}
        errors: list[BaseException] = []

        def start_stop(sandbox_id: str) -> None:
            sandbox = sandbox_module.Sandbox(
                make_sandbox_dto(sandbox_id=sandbox_id),
                mock_toolbox_api_client,
                sandbox_api,
                "python",
                subscription_manager=manager,
                http_client=MagicMock(),
            )

            def run() -> None:
                try:
                    sandbox.stop(timeout=0)
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    errors.append(exc)

            # Daemon: a failed assertion must not leave a polling wait that blocks interpreter exit.
            threads[sandbox_id] = threading.Thread(target=run, name=sandbox_id, daemon=True)
            threads[sandbox_id].start()
            _wait_until(lambda: sandbox_id in polls)

        for sandbox_id in ("sb-1", "sb-2", "sb-3"):
            start_stop(sandbox_id)

        assert _sent(sio) == [("subscribe", ["sb-1"]), ("subscribe", ["sb-2"])]
        # The overflow wait reads first and polls at the subscribed rate; the others skip and wait 5 s.
        assert [c.args[0] for c in sandbox_api.get_sandbox.call_args_list] == ["sb-3"]
        assert polls == {"sb-1": [pytest.approx(5.0)], "sb-2": [pytest.approx(5.0)], "sb-3": [pytest.approx(1.0)]}

        dispatcher._dispatch("sb-1", STATE_EVENT, _stopped("sb-1"))
        threads["sb-1"].join(timeout=2)
        assert not threads["sb-1"].is_alive()
        assert _sent(sio)[-1] == ("unsubscribe", ["sb-1"])
        assert not dispatcher.is_served("sb-3")

        start_stop("sb-4")
        assert _sent(sio)[-1] == ("subscribe", ["sb-4"])
        assert polls["sb-4"] == [pytest.approx(5.0)]
        assert [c.args[0] for c in sandbox_api.get_sandbox.call_args_list] == ["sb-3"]

        for sandbox_id in ("sb-2", "sb-3", "sb-4"):
            dispatcher._dispatch(sandbox_id, STATE_EVENT, _stopped(sandbox_id))
        for thread in threads.values():
            thread.join(timeout=2)
        assert not errors
        manager.shutdown()
        dispatcher.disconnect()


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
