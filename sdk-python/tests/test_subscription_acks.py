# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Subscribe acknowledgements: a rejected id stops being event-backed, and its waits read and poll."""

from __future__ import annotations

import asyncio
import collections
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daytona.internal import event_dispatcher as event_dispatcher_module
from daytona.internal import wait_poll
from daytona.internal.event_dispatcher import SUBSCRIPTION_REJECTED_EVENT
from daytona.internal.event_subscription_manager import AsyncEventSubscriptionManager, SyncEventSubscriptionManager
from daytona_api_client import SandboxState
from daytona_api_client_async import Sandbox as AsyncSandboxDto

from .conftest import make_sandbox_dto
from .test_event_dispatcher import FakeAsyncClient, FakeSyncClient
from .test_subscription_cap import (
    STATE_EVENT,
    _async_wait_until,
    _connected_async_dispatcher,
    _connected_sync_dispatcher,
    _sent,
    _settle,
    _wait_until,
)

REJECTED = {"ok": False, "error": "OVERLOADED", "message": "The gateway is overloaded"}


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


def _subscribe_index(sio: FakeAsyncClient | FakeSyncClient, resource_id: str) -> int:
    """Index of the last subscribe message that carried the id."""
    return max(
        i for i, (event, data) in enumerate(sio.emitted) if event == "subscribe" and resource_id in data["sandboxIds"]
    )


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def __call__(self, event_name: str, data: Any) -> None:
        self.events.append((event_name, data))


def test_the_per_connection_limit_is_ten_thousand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the autouse fixture lowers it for the other tests
    assert event_dispatcher_module.MAX_SUBSCRIPTIONS_PER_CONNECTION == 10_000


class TestAsyncSubscribeAcks:
    async def test_subscribe_asks_for_an_ack_and_unsubscribe_does_not(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        unsub = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        await _settle()
        unsub()
        await _settle()
        sio = async_sio.instances[0]

        assert _sent(sio) == [("subscribe", ["sb-1"]), ("unsubscribe", ["sb-1"])]
        assert sio.sent_callbacks[0] is not None and sio.sent_callbacks[1] is None
        await dispatcher.disconnect()

    async def test_rejected_id_is_unserved_frees_its_slot_and_stays_out_of_the_reconnect_set(
        self, async_sio, monkeypatch
    ):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        manager = AsyncEventSubscriptionManager(dispatcher)
        handler = _Recorder()
        _ = manager.subscribe("sb-1", handler, [STATE_EVENT])
        _ = manager.subscribe("sb-2", _Recorder(), [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]
        assert manager.is_event_backed("sb-1")

        await sio.ack(_subscribe_index(sio, "sb-1"), REJECTED)

        assert not dispatcher.is_served("sb-1") and dispatcher.is_rejected("sb-1")
        assert not manager.is_event_backed("sb-1")
        assert handler.events == [
            (SUBSCRIPTION_REJECTED_EVENT, {"error": "OVERLOADED", "message": "The gateway is overloaded"})
        ]
        # The slot is free again: the next new id is served.
        _ = manager.subscribe("sb-3", _Recorder(), [STATE_EVENT])
        await _settle()
        assert dispatcher.is_served("sb-3")
        assert _sent(sio)[-1] == ("subscribe", ["sb-3"])

        await sio.simulate_disconnect()
        sio.emitted.clear()
        await sio.simulate_connect()
        assert _sent(sio) == [("subscribe", ["sb-2", "sb-3"])]
        assert dispatcher.is_rejected("sb-1")  # no promotion
        manager.shutdown()
        await dispatcher.disconnect()

    async def test_last_listener_of_a_rejected_id_leaves_without_an_unsubscribe(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        unsub = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]
        await sio.ack(_subscribe_index(sio, "sb-1"), REJECTED)
        sio.emitted.clear()

        unsub()
        await _settle()

        assert sio.emitted == []
        assert not dispatcher.is_rejected("sb-1")
        # A new first listener is served again.
        _ = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        await _settle()
        assert dispatcher.is_served("sb-1")
        await dispatcher.disconnect()

    async def test_a_chunk_rejection_skips_ids_that_lost_their_listeners(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        sio = async_sio.instances[0]
        await sio.simulate_disconnect()
        unsub_1 = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        _ = dispatcher.subscribe("sb-2", _Recorder(), [STATE_EVENT])
        await sio.simulate_connect()  # one chunk: [sb-1, sb-2]
        unsub_1()
        await _settle()

        await sio.ack(_subscribe_index(sio, "sb-2"), REJECTED)

        assert dispatcher.is_rejected("sb-2")
        assert not dispatcher.is_rejected("sb-1")
        await dispatcher.disconnect()

    @pytest.mark.parametrize("ack", [{"ok": True}, None, "unexpected"])
    async def test_an_accepting_or_unrecognized_ack_changes_nothing(self, async_sio, monkeypatch, ack):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        handler = _Recorder()
        _ = dispatcher.subscribe("sb-1", handler, [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]

        if ack is None:
            await sio.ack(_subscribe_index(sio, "sb-1"))
        else:
            await sio.ack(_subscribe_index(sio, "sb-1"), ack)

        assert dispatcher.is_served("sb-1") and not dispatcher.is_rejected("sb-1")
        assert dispatcher.is_event_backed("sb-1")
        assert handler.events == []
        await dispatcher.disconnect()

    async def test_no_ack_changes_nothing_and_the_pending_ack_is_dropped_after_the_timeout(
        self, async_sio, monkeypatch
    ):
        monkeypatch.setattr(event_dispatcher_module, "_ACK_TIMEOUT", 0.0)
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        _ = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]
        assert len(sio.callbacks["/"]) == 1

        _ = dispatcher.subscribe("sb-2", _Recorder(), [STATE_EVENT])  # prunes the expired pending ack
        await _settle()

        assert list(sio.callbacks["/"].values()) == [sio.sent_callbacks[_subscribe_index(sio, "sb-2")]]
        # A late ack for the dropped message is unknown and ignored.
        await sio.ack(_subscribe_index(sio, "sb-1"), REJECTED)
        assert dispatcher.is_served("sb-1") and dispatcher.is_event_backed("sb-1")
        await dispatcher.disconnect()

    async def test_an_ack_from_before_a_reconnect_is_ignored(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        _ = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]
        stale_ack = sio.sent_callbacks[_subscribe_index(sio, "sb-1")]
        await sio.simulate_disconnect()
        await sio.simulate_connect()

        await stale_ack(REJECTED)

        assert dispatcher.is_served("sb-1") and not dispatcher.is_rejected("sb-1")
        await dispatcher.disconnect()

    async def test_a_late_rejection_of_an_earlier_subscribe_leaves_a_resubscription_alone(self, async_sio, monkeypatch):
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        unsub = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        await _settle()
        unsub()
        await _settle()
        handler = _Recorder()
        _ = dispatcher.subscribe("sb-1", handler, [STATE_EVENT])
        await _settle()
        sio = async_sio.instances[0]
        assert _sent(sio) == [("subscribe", ["sb-1"]), ("unsubscribe", ["sb-1"]), ("subscribe", ["sb-1"])]

        await sio.ack(0, REJECTED)  # the first subscribe's answer arrives late

        assert dispatcher.is_served("sb-1") and dispatcher.is_event_backed("sb-1")
        assert not dispatcher.is_rejected("sb-1")
        assert handler.events == []
        await dispatcher.disconnect()


class TestSyncSubscribeAcks:
    def test_subscribe_asks_for_an_ack_and_unsubscribe_does_not(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        unsub = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        unsub()
        sio = sync_sio.instances[0]

        assert _sent(sio) == [("subscribe", ["sb-1"]), ("unsubscribe", ["sb-1"])]
        assert sio.sent_callbacks[0] is not None and sio.sent_callbacks[1] is None
        dispatcher.disconnect()

    def test_rejected_id_is_unserved_frees_its_slot_and_stays_out_of_the_reconnect_set(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        manager = SyncEventSubscriptionManager(dispatcher)
        handler = _Recorder()
        _ = manager.subscribe("sb-1", handler, [STATE_EVENT])
        _ = manager.subscribe("sb-2", _Recorder(), [STATE_EVENT])
        sio = sync_sio.instances[0]
        assert manager.is_event_backed("sb-1")

        # Acks run on the socket's thread.
        acker = threading.Thread(target=sio.ack, args=(_subscribe_index(sio, "sb-1"), REJECTED))
        acker.start()
        acker.join()

        assert not dispatcher.is_served("sb-1") and dispatcher.is_rejected("sb-1")
        assert not manager.is_event_backed("sb-1")
        assert handler.events == [
            (SUBSCRIPTION_REJECTED_EVENT, {"error": "OVERLOADED", "message": "The gateway is overloaded"})
        ]
        _ = manager.subscribe("sb-3", _Recorder(), [STATE_EVENT])
        assert dispatcher.is_served("sb-3")
        assert _sent(sio)[-1] == ("subscribe", ["sb-3"])

        sio.simulate_disconnect()
        sio.emitted.clear()
        sio.simulate_connect()
        assert _sent(sio) == [("subscribe", ["sb-2", "sb-3"])]
        assert dispatcher.is_rejected("sb-1")  # no promotion
        manager.shutdown()
        dispatcher.disconnect()

    def test_last_listener_of_a_rejected_id_leaves_without_an_unsubscribe(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        unsub = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        sio = sync_sio.instances[0]
        sio.ack(_subscribe_index(sio, "sb-1"), REJECTED)
        sio.emitted.clear()

        unsub()

        assert sio.emitted == []
        assert not dispatcher.is_rejected("sb-1")
        _ = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        assert dispatcher.is_served("sb-1")
        dispatcher.disconnect()

    @pytest.mark.parametrize("ack", [{"ok": True}, None, "unexpected"])
    def test_an_accepting_or_unrecognized_ack_changes_nothing(self, sync_sio, monkeypatch, ack):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        handler = _Recorder()
        _ = dispatcher.subscribe("sb-1", handler, [STATE_EVENT])
        sio = sync_sio.instances[0]

        if ack is None:
            sio.ack(_subscribe_index(sio, "sb-1"))
        else:
            sio.ack(_subscribe_index(sio, "sb-1"), ack)

        assert dispatcher.is_served("sb-1") and not dispatcher.is_rejected("sb-1")
        assert dispatcher.is_event_backed("sb-1")
        assert handler.events == []
        dispatcher.disconnect()

    def test_no_ack_changes_nothing_and_the_pending_ack_is_dropped_after_the_timeout(self, sync_sio, monkeypatch):
        monkeypatch.setattr(event_dispatcher_module, "_ACK_TIMEOUT", 0.0)
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        _ = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        sio = sync_sio.instances[0]
        assert len(sio.callbacks["/"]) == 1

        _ = dispatcher.subscribe("sb-2", _Recorder(), [STATE_EVENT])

        assert list(sio.callbacks["/"].values()) == [sio.sent_callbacks[_subscribe_index(sio, "sb-2")]]
        sio.ack(_subscribe_index(sio, "sb-1"), REJECTED)
        assert dispatcher.is_served("sb-1") and dispatcher.is_event_backed("sb-1")
        dispatcher.disconnect()

    def test_an_ack_from_before_a_reconnect_is_ignored(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        _ = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        sio = sync_sio.instances[0]
        stale_ack = sio.sent_callbacks[_subscribe_index(sio, "sb-1")]
        sio.simulate_disconnect()
        sio.simulate_connect()

        stale_ack(REJECTED)

        assert dispatcher.is_served("sb-1") and not dispatcher.is_rejected("sb-1")
        dispatcher.disconnect()

    def test_a_late_rejection_of_an_earlier_subscribe_leaves_a_resubscription_alone(self, sync_sio, monkeypatch):
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        unsub = dispatcher.subscribe("sb-1", _Recorder(), [STATE_EVENT])
        unsub()
        handler = _Recorder()
        _ = dispatcher.subscribe("sb-1", handler, [STATE_EVENT])
        sio = sync_sio.instances[0]
        assert _sent(sio) == [("subscribe", ["sb-1"]), ("unsubscribe", ["sb-1"]), ("subscribe", ["sb-1"])]

        acker = threading.Thread(target=sio.ack, args=(0, REJECTED))  # the first subscribe's late answer
        acker.start()
        acker.join()

        assert dispatcher.is_served("sb-1") and dispatcher.is_event_backed("sb-1")
        assert not dispatcher.is_rejected("sb-1")
        assert handler.events == []
        dispatcher.disconnect()


class TestAsyncWaitOnARejectedSubscription:
    async def test_a_skipping_wait_reads_at_once_then_falls_back(
        self, async_sio, monkeypatch, mock_async_toolbox_api_client
    ):
        from daytona._async import sandbox as sandbox_module

        intervals: list[float] = []
        real_wait_for = asyncio.wait_for

        class RecordingAsyncio:
            """The sandbox module's ``asyncio``: the first wait blocks until woken, later ones time out at once."""

            def __getattr__(self, name: str):
                return getattr(asyncio, name)

            @staticmethod
            async def wait_for(awaitable, timeout):
                intervals.append(timeout)
                return await real_wait_for(awaitable, timeout=None if len(intervals) == 1 else 0)

        monkeypatch.setattr(sandbox_module, "asyncio", RecordingAsyncio())
        dispatcher = await _connected_async_dispatcher(monkeypatch)
        manager = AsyncEventSubscriptionManager(dispatcher)
        sio = async_sio.instances[0]
        sandbox_api = AsyncMock()

        def dto(state: SandboxState) -> AsyncSandboxDto:
            return AsyncSandboxDto(**make_sandbox_dto(state=state).model_dump())

        sandbox_api.stop_sandbox = AsyncMock(return_value=dto(SandboxState.STOPPING))
        sandbox_api.get_sandbox = AsyncMock(
            side_effect=[dto(SandboxState.STOPPING), dto(SandboxState.STOPPING), dto(SandboxState.STOPPED)]
        )
        sandbox = sandbox_module.AsyncSandbox(
            make_sandbox_dto(), mock_async_toolbox_api_client, sandbox_api, "python", subscription_manager=manager
        )

        task = asyncio.create_task(sandbox.stop(timeout=0))
        await _async_wait_until(lambda: len(intervals) == 1)
        assert intervals == [pytest.approx(5.0)]  # event-backed: skipped its first read
        sandbox_api.get_sandbox.assert_not_called()

        await sio.ack(_subscribe_index(sio, "test-sandbox-id"), REJECTED)
        await real_wait_for(task, timeout=2)

        # Woken by the rejection: one read at once, then the fallback backoff from 1 s.
        assert sandbox_api.get_sandbox.await_count == 3
        assert intervals == [pytest.approx(5.0), pytest.approx(1.0), pytest.approx(1.5)]
        assert sandbox.state == SandboxState.STOPPED
        manager.shutdown()
        await dispatcher.disconnect()


class TestSyncWaitOnARejectedSubscription:
    def test_a_skipping_wait_reads_at_once_then_falls_back(self, sync_sio, monkeypatch, mock_toolbox_api_client):
        from daytona._sync import sandbox as sandbox_module

        intervals: list[float] = []

        def wait_for_wake(wake: threading.Event, timeout: float) -> bool:
            """The first wait blocks until woken, later ones time out at once."""
            intervals.append(timeout)
            return wake.wait(5 if len(intervals) == 1 else 0)

        monkeypatch.setattr(sandbox_module, "_wait_for_wake", wait_for_wake)
        dispatcher = _connected_sync_dispatcher(monkeypatch)
        manager = SyncEventSubscriptionManager(dispatcher)
        sio = sync_sio.instances[0]
        sandbox_api = MagicMock()
        sandbox_api.stop_sandbox.return_value = make_sandbox_dto(state=SandboxState.STOPPING)
        sandbox_api.get_sandbox.side_effect = [
            make_sandbox_dto(state=SandboxState.STOPPING),
            make_sandbox_dto(state=SandboxState.STOPPING),
            make_sandbox_dto(state=SandboxState.STOPPED),
        ]
        sandbox = sandbox_module.Sandbox(
            make_sandbox_dto(),
            mock_toolbox_api_client,
            sandbox_api,
            "python",
            subscription_manager=manager,
            http_client=MagicMock(),
        )
        errors: list[BaseException] = []

        def run() -> None:
            try:
                sandbox.stop(timeout=0)
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                errors.append(exc)

        waiter = threading.Thread(target=run, daemon=True)
        waiter.start()
        _wait_until(lambda: len(intervals) == 1)
        assert intervals == [pytest.approx(5.0)]  # event-backed: skipped its first read
        sandbox_api.get_sandbox.assert_not_called()

        acker = threading.Thread(target=sio.ack, args=(_subscribe_index(sio, "test-sandbox-id"), REJECTED))
        acker.start()
        acker.join()
        waiter.join(timeout=4)

        assert not waiter.is_alive() and not errors
        # Woken by the rejection: one read at once, then the fallback backoff from 1 s.
        assert sandbox_api.get_sandbox.call_count == 3
        assert intervals == [pytest.approx(5.0), pytest.approx(1.0), pytest.approx(1.5)]
        assert sandbox.state == SandboxState.STOPPED
        manager.shutdown()
        dispatcher.disconnect()


class TestPendingAckPruning:
    @staticmethod
    def _client_with_pending(count: int) -> tuple[MagicMock, Any, list[Any]]:
        sio = MagicMock()
        callbacks = [lambda *_args: None for _ in range(count)]
        sio.callbacks = {"/": {0: object(), **{i + 1: cb for i, cb in enumerate(callbacks)}}}
        pending: Any = collections.deque()
        return sio, pending, callbacks

    def test_removes_exactly_the_expired_acks(self) -> None:
        sio, pending, callbacks = self._client_with_pending(4)
        now = time.monotonic()
        for i, callback in enumerate(callbacks):
            pending.append((now - 1 if i < 2 else now + 60, callback))
        acked = callbacks[1]
        del sio.callbacks["/"][2]  # already answered: no longer in the table, still in the deque

        event_dispatcher_module._prune_pending_acks(sio, pending)

        assert list(sio.callbacks["/"].values())[1:] == callbacks[2:]
        assert 0 in sio.callbacks["/"]  # python-socketio's id counter entry is left alone
        assert [cb for _, cb in pending] == callbacks[2:]
        assert acked not in sio.callbacks["/"].values()

    def test_pruning_ten_thousand_expired_acks_is_linear(self) -> None:
        sio, pending, callbacks = self._client_with_pending(10_000)
        expired_at = time.monotonic() - 1
        for callback in callbacks:
            pending.append((expired_at, callback))

        snapshots = 0
        real_list = list

        def counting_list(iterable=()):  # counts copies of the pending table
            nonlocal snapshots
            if isinstance(iterable, type({}.items())):
                snapshots += 1
            return real_list(iterable)

        with patch.object(event_dispatcher_module, "list", counting_list, create=True):
            event_dispatcher_module._prune_pending_acks(sio, pending)

        assert list(sio.callbacks["/"]) == [0] and not pending
        # One snapshot of the table, not one per expired callback.
        assert snapshots <= 1
