# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from daytona.internal.event_subscription_manager import SyncEventSubscriptionManager


class FakeSyncDispatcher:
    """Minimal SyncEventDispatcher stand-in that records subscribe/unsubscribe calls."""

    def __init__(self, failing_resources: set[str] | None = None) -> None:
        self.subscribe_calls: list[str] = []
        self.unsubscribed: list[str] = []
        self._failing_resources = failing_resources or set()
        self._lock = threading.Lock()

    def subscribe(
        self,
        resource_id: str,
        handler: Callable[[str, Any], None],
        events: list[str],
    ) -> Callable[[], None]:
        del handler, events
        with self._lock:
            self.subscribe_calls.append(resource_id)

        def unsubscribe() -> None:
            if resource_id in self._failing_resources:
                raise RuntimeError(f"unsubscribe failed for {resource_id}")
            with self._lock:
                self.unsubscribed.append(resource_id)

        return unsubscribe


def _noop_handler(_event_name: str, _data: Any) -> None:
    pass


def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class TestSyncEventSubscriptionManagerThreadUsage:
    def test_many_subscriptions_share_one_expiry_thread(self):
        """Regression test for daytona/clients#108: listing ~10k sandboxes created
        one threading.Timer (OS thread) per subscription and OOMed 512MiB containers.
        """
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher)
        threads_before = threading.active_count()

        sub_ids = [manager.subscribe(f"sandbox-{i}", _noop_handler, ["sandbox.state.updated"]) for i in range(500)]

        threads_after = threading.active_count()
        assert all(sub_ids)
        # A single shared expiry worker is allowed; one thread per subscription is the bug.
        assert threads_after - threads_before <= 1
        manager.shutdown()

    def test_refresh_does_not_spawn_threads(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher)
        sub_id = manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])
        threads_before = threading.active_count()

        for _ in range(100):
            assert manager.refresh(sub_id)

        assert threading.active_count() == threads_before
        manager.shutdown()


class TestSyncEventSubscriptionManagerTtl:
    def test_subscription_expires_after_ttl(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=0.1)

        sub_id = manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])

        assert _wait_until(lambda: dispatcher.unsubscribed == ["sandbox-1"])
        assert not manager.refresh(sub_id)

    def test_refresh_extends_ttl(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=0.3)

        sub_id = manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])
        # Keep refreshing past the original deadline.
        for _ in range(4):
            time.sleep(0.15)
            assert manager.refresh(sub_id)
        assert dispatcher.unsubscribed == []

        # Stop refreshing: the subscription must now expire.
        assert _wait_until(lambda: dispatcher.unsubscribed == ["sandbox-1"])
        manager.shutdown()

    def test_subscribe_works_after_all_subscriptions_expired(self):
        """The expiry worker may exit when idle; a later subscribe must still expire."""
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=0.1)

        manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])
        assert _wait_until(lambda: dispatcher.unsubscribed == ["sandbox-1"])

        manager.subscribe("sandbox-2", _noop_handler, ["sandbox.state.updated"])
        assert _wait_until(lambda: dispatcher.unsubscribed == ["sandbox-1", "sandbox-2"])
        manager.shutdown()


class TestSyncEventSubscriptionManagerLifecycle:
    def test_unsubscribe_calls_dispatcher_once(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher)
        sub_id = manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])

        manager.unsubscribe(sub_id)
        manager.unsubscribe(sub_id)

        assert dispatcher.unsubscribed == ["sandbox-1"]
        assert not manager.refresh(sub_id)
        manager.shutdown()

    def test_shutdown_unsubscribes_all_and_blocks_new_subscriptions(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher)
        manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])
        manager.subscribe("sandbox-2", _noop_handler, ["sandbox.state.updated"])

        manager.shutdown()

        assert sorted(dispatcher.unsubscribed) == ["sandbox-1", "sandbox-2"]
        assert manager.subscribe("sandbox-3", _noop_handler, ["sandbox.state.updated"]) == ""

    def test_shutdown_stops_expiry_worker(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=60.0)
        manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])
        assert threading.active_count() >= 2

        threads_expected = threading.active_count() - 1
        manager.shutdown()

        assert _wait_until(lambda: threading.active_count() == threads_expected)

    def test_subscribe_without_dispatcher_is_noop(self):
        manager = SyncEventSubscriptionManager(None)
        assert manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"]) == ""
        assert not manager.refresh("missing")


class TestSyncEventSubscriptionManagerRobustness:
    def test_raising_unsubscribe_does_not_kill_the_expiry_worker(self):
        """A failing dispatcher unsubscribe for one subscription must not stop
        TTL expiry for every other subscription (the old per-thread design only
        lost the one thread; the shared worker must be at least as robust).
        """
        dispatcher = FakeSyncDispatcher(failing_resources={"sandbox-faulty"})
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=0.1)

        manager.subscribe("sandbox-faulty", _noop_handler, ["sandbox.state.updated"])
        manager.subscribe("sandbox-good", _noop_handler, ["sandbox.state.updated"])

        assert _wait_until(lambda: "sandbox-good" in dispatcher.unsubscribed)
        manager.shutdown()

    def test_worker_is_replaced_when_it_died_unexpectedly(self):
        """A crashed worker leaves a dead-but-set thread reference; the next
        subscribe must detect it via is_alive() and start a replacement.
        """
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=0.1)

        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()
        with manager._cond:  # pylint: disable=protected-access
            manager._worker = dead_thread  # pylint: disable=protected-access

        manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])

        assert _wait_until(lambda: dispatcher.unsubscribed == ["sandbox-1"])
        manager.shutdown()

    def test_worker_exits_promptly_after_last_unsubscribe(self):
        dispatcher = FakeSyncDispatcher()
        manager = SyncEventSubscriptionManager(dispatcher, ttl_seconds=60.0)
        threads_before = threading.active_count()
        sub_id = manager.subscribe("sandbox-1", _noop_handler, ["sandbox.state.updated"])

        manager.unsubscribe(sub_id)

        assert _wait_until(lambda: threading.active_count() == threads_before, timeout=2.0)
        manager.shutdown()
