# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import heapq
import logging
import threading
import time
import uuid
from typing import Callable

from .event_dispatcher import AsyncEventDispatcher, AsyncEventHandler, EventHandler, SyncEventDispatcher

logger = logging.getLogger(__name__)

_SUBSCRIPTION_TTL: float = 300.0


class _Subscription:
    __slots__: tuple[str, ...] = ("resource_id", "unsubscribe_fn", "timer")

    resource_id: str
    unsubscribe_fn: Callable[[], None]
    timer: asyncio.TimerHandle | None

    def __init__(
        self,
        resource_id: str,
        unsubscribe_fn: Callable[[], None],
    ) -> None:
        self.resource_id = resource_id
        self.unsubscribe_fn = unsubscribe_fn
        self.timer = None


class _SyncSubscription:
    __slots__: tuple[str, ...] = ("resource_id", "unsubscribe_fn", "expires_at")

    resource_id: str
    unsubscribe_fn: Callable[[], None]
    expires_at: float

    def __init__(
        self,
        resource_id: str,
        unsubscribe_fn: Callable[[], None],
        expires_at: float,
    ) -> None:
        self.resource_id = resource_id
        self.unsubscribe_fn = unsubscribe_fn
        self.expires_at = expires_at


class AsyncEventSubscriptionManager:
    """Tracks subscriptions by unique sub_id with optional TTL auto-expiry.

    Multiple callers subscribing to the same resource_id get independent sub_ids.
    """

    _dispatcher: AsyncEventDispatcher | None
    _subscriptions: dict[str, _Subscription]
    _closed: bool

    def __init__(self, dispatcher: AsyncEventDispatcher | None) -> None:
        self._dispatcher = dispatcher
        self._subscriptions = {}
        self._closed = False

    @property
    def dispatcher(self) -> AsyncEventDispatcher | None:
        return self._dispatcher

    def subscribe(
        self,
        resource_id: str,
        handler: AsyncEventHandler,
        events: list[str],
    ) -> str:
        if self._closed or self._dispatcher is None:
            return ""

        unsubscribe_fn = self._dispatcher.subscribe(resource_id, handler, events)

        sub_id = uuid.uuid4().hex
        sub = _Subscription(resource_id=resource_id, unsubscribe_fn=unsubscribe_fn)
        self._subscriptions[sub_id] = sub
        self._start_timer(sub_id)

        return sub_id

    def refresh(self, sub_id: str) -> bool:
        if self._closed:
            return False

        sub = self._subscriptions.get(sub_id)
        if sub is None:
            return False

        self._start_timer(sub_id)
        return True

    def unsubscribe(self, sub_id: str) -> None:
        sub = self._subscriptions.pop(sub_id, None)
        if sub is None:
            return

        if sub.timer is not None:
            sub.timer.cancel()
        sub.unsubscribe_fn()

    def _start_timer(self, sub_id: str) -> None:
        sub = self._subscriptions.get(sub_id)
        if sub is None:
            return

        if sub.timer is not None:
            sub.timer.cancel()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def _expire() -> None:
            popped = self._subscriptions.pop(sub_id, None)
            if popped is not None:
                popped.unsubscribe_fn()

        sub.timer = loop.call_later(_SUBSCRIPTION_TTL, _expire)

    def shutdown(self) -> None:
        self._closed = True
        for sub in self._subscriptions.values():
            if sub.timer is not None:
                sub.timer.cancel()
            sub.unsubscribe_fn()
        self._subscriptions.clear()


class SyncEventSubscriptionManager:
    """Thread-safe variant of AsyncEventSubscriptionManager.

    All subscriptions share ONE lazily started expiry worker thread instead of a
    ``threading.Timer`` (a dedicated OS thread) per subscription. Listing many
    sandboxes therefore costs one thread total, not one thread each
    (https://github.com/daytona/clients/issues/108). ``refresh()`` only bumps the
    subscription deadline — it never creates or cancels threads.
    """

    _dispatcher: SyncEventDispatcher | None
    _subscriptions: dict[str, _SyncSubscription]
    _expiry_heap: list[tuple[float, str]]
    _cond: threading.Condition
    _worker: threading.Thread | None
    _ttl: float
    _closed: bool

    def __init__(self, dispatcher: SyncEventDispatcher | None, ttl_seconds: float = _SUBSCRIPTION_TTL) -> None:
        self._dispatcher = dispatcher
        self._subscriptions = {}
        # Min-heap of (deadline, sub_id). Entries are lazily invalidated: a popped
        # entry is discarded when its subscription is gone, or re-pushed at the
        # current deadline when the subscription was refreshed in the meantime.
        # Invariant: every live subscription has at least one heap entry.
        self._expiry_heap = []
        self._cond = threading.Condition()
        self._worker = None
        self._ttl = ttl_seconds
        self._closed = False

    @property
    def dispatcher(self) -> SyncEventDispatcher | None:
        return self._dispatcher

    def subscribe(
        self,
        resource_id: str,
        handler: EventHandler,
        events: list[str],
    ) -> str:
        if self._closed or self._dispatcher is None:
            return ""

        unsubscribe_fn = self._dispatcher.subscribe(resource_id, handler, events)

        sub_id = uuid.uuid4().hex
        sub = _SyncSubscription(
            resource_id=resource_id,
            unsubscribe_fn=unsubscribe_fn,
            expires_at=time.monotonic() + self._ttl,
        )

        with self._cond:
            if self._closed:
                # shutdown() raced between the check above and here — undo the
                # dispatcher listener we just created instead of leaking it.
                unsubscribe_fn()
                return ""
            self._subscriptions[sub_id] = sub
            heapq.heappush(self._expiry_heap, (sub.expires_at, sub_id))
            self._ensure_worker_locked()
            self._cond.notify()

        return sub_id

    def refresh(self, sub_id: str) -> bool:
        with self._cond:
            if self._closed:
                return False

            sub = self._subscriptions.get(sub_id)
            if sub is None:
                return False

            # No heap entry or worker wake-up needed: when the old deadline pops,
            # the worker sees the newer expires_at and re-queues the subscription.
            sub.expires_at = time.monotonic() + self._ttl
            return True

    def unsubscribe(self, sub_id: str) -> None:
        with self._cond:
            sub = self._subscriptions.pop(sub_id, None)
            if sub is None:
                return
            # The stale heap entry is discarded when the worker pops it — except
            # when it was the last subscription: drop the leftovers and wake the
            # worker so it exits now instead of idling until the old deadline.
            if not self._subscriptions:
                self._expiry_heap.clear()
                self._cond.notify()

        sub.unsubscribe_fn()

    def _ensure_worker_locked(self) -> None:
        """Start the shared expiry worker if it is not running. Caller holds self._cond.

        The is_alive() check is a safety net: a worker that crashed for any reason
        leaves a dead-but-set reference, and a plain None check would then block
        replacements forever, silently disabling expiry.
        """
        if self._worker is None or not self._worker.is_alive():
            worker = threading.Thread(
                target=self._expiry_loop,
                name="daytona-subscription-expiry",
                daemon=True,
            )
            self._worker = worker
            worker.start()

    def _expiry_loop(self) -> None:
        while True:
            expired: list[_SyncSubscription] = []
            with self._cond:
                while not expired:
                    if self._closed:
                        return
                    if not self._expiry_heap:
                        # No live subscriptions remain (see heap invariant above):
                        # exit and let the next subscribe() start a fresh worker.
                        self._worker = None
                        return
                    now = time.monotonic()
                    deadline = self._expiry_heap[0][0]
                    if deadline > now:
                        _ = self._cond.wait(deadline - now)
                        continue
                    while self._expiry_heap and self._expiry_heap[0][0] <= now:
                        _, sub_id = heapq.heappop(self._expiry_heap)
                        sub = self._subscriptions.get(sub_id)
                        if sub is None:
                            continue
                        if sub.expires_at > now:
                            heapq.heappush(self._expiry_heap, (sub.expires_at, sub_id))
                            continue
                        del self._subscriptions[sub_id]
                        expired.append(sub)

            for sub in expired:
                try:
                    sub.unsubscribe_fn()
                except Exception:  # pylint: disable=broad-exception-caught
                    # One failing dispatcher unsubscribe must not kill the shared
                    # worker and silently stop expiry for every other subscription.
                    logger.debug("Unsubscribe error for %s", sub.resource_id, exc_info=True)

    def shutdown(self) -> None:
        with self._cond:
            self._closed = True
            subs = list(self._subscriptions.values())
            self._subscriptions.clear()
            self._expiry_heap.clear()
            self._cond.notify_all()

        for sub in subs:
            sub.unsubscribe_fn()
