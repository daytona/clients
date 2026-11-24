# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Race a file transfer's network waits against its cancel signal.

A transfer with no signal awaits the network directly. An ``asyncio.Event``
or a ``threading.Event`` wakes the transfer when it is set, with no periodic
wake-ups. Any other object with ``is_set()`` cannot notify anyone, so it is
checked every ``POLL_SECONDS`` while the transfer waits, and only then.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import threading
from types import TracebackType
from typing import Awaitable, Callable, Final, Optional, Type, TypeVar

from typing_extensions import Self

from .errors import TransferCancelledError
from .types import TransferCancelSignal

_T = TypeVar("_T")

POLL_SECONDS: Final[float] = 0.05


def raise_if_transfer_cancelled(cancel: Optional[TransferCancelSignal], *, operation: str) -> None:
    if cancel is not None and cancel.is_set():
        raise TransferCancelledError(
            f"{operation} transfer cancelled", status_code=499, code="REQUEST_CANCELLED", source="SDK"
        )


async def _first_done(task: asyncio.Task[_T], notified: asyncio.Future[object]) -> None:
    woken: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def wake(_: object) -> None:
        if not woken.done():
            woken.set_result(None)

    task.add_done_callback(wake)
    notified.add_done_callback(wake)
    try:
        await woken
    finally:
        task.remove_done_callback(wake)
        notified.remove_done_callback(wake)


class _ThreadEventWatch:
    """Calls ``on_set`` from a helper thread when a ``threading.Event`` is set.

    ``Event.set()`` releases every lock queued on the event's condition, so
    the helper queues its own lock there and blocks on it. ``close()`` takes
    the lock off the queue and releases it itself, so the helper ends whether
    or not the event was ever set: no thread outlives the transfer.
    """

    def __init__(self, condition: threading.Condition, on_set: Callable[[], None]) -> None:
        self._condition = condition
        self._on_set = on_set
        self._closed = False
        self._wake = threading.Lock()
        self._wake.acquire()
        self.thread = threading.Thread(target=self._run, name="daytona-transfer-cancel", daemon=True)

    @staticmethod
    def condition_of(event: object) -> Optional[threading.Condition]:
        """The condition a plain ``threading.Event`` notifies, else None."""
        if type(event) is not threading.Event:
            return None
        condition = getattr(event, "_cond", None)
        if not isinstance(condition, threading.Condition):
            return None
        if not isinstance(getattr(condition, "_waiters", None), collections.deque):
            return None
        return condition

    def start(self, event: threading.Event) -> None:
        with self._condition:
            if event.is_set():
                self._wake.release()
            else:
                self._condition._waiters.append(self._wake)  # type: ignore[attr-defined]
        self.thread.start()

    def _run(self) -> None:
        self._wake.acquire()
        if not self._closed:
            self._on_set()

    def close(self) -> None:
        self._closed = True
        with self._condition, contextlib.suppress(ValueError):
            self._condition._waiters.remove(self._wake)  # type: ignore[attr-defined]
        with contextlib.suppress(RuntimeError):
            self._wake.release()
        self.thread.join()


class TransferCancelWatch:
    """Watches one transfer's cancel signal for as long as the transfer runs.

    Use as ``async with TransferCancelWatch(cancel) as watch`` and await each
    network step through ``watch.run(step, operation=...)``.
    """

    def __init__(self, cancel: Optional[TransferCancelSignal]) -> None:
        self.cancel = cancel
        self._notifier: Optional[asyncio.Future[object]] = None
        self._notified: Optional[asyncio.Future[object]] = None
        self._thread_watch: Optional[_ThreadEventWatch] = None

    @property
    def polling(self) -> bool:
        """Whether a wait on the network wakes every ``POLL_SECONDS`` to check the signal."""
        return self.cancel is not None and self._notified is None

    async def __aenter__(self) -> Self:
        cancel = self.cancel
        if cancel is None:
            return self
        if isinstance(cancel, asyncio.Event):
            self._notifier = self._notified = asyncio.ensure_future(cancel.wait())
            return self
        condition = _ThreadEventWatch.condition_of(cancel)
        if condition is not None:
            loop = asyncio.get_running_loop()
            notified: asyncio.Future[object] = loop.create_future()

            def resolve() -> None:
                if not notified.done():
                    notified.set_result(None)

            def on_set() -> None:
                with contextlib.suppress(RuntimeError):  # the loop has closed
                    loop.call_soon_threadsafe(resolve)

            watch = _ThreadEventWatch(condition, on_set)
            watch.start(cancel)  # type: ignore[arg-type]
            self._thread_watch = watch
            self._notifier = self._notified = notified
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        notified, self._notifier, self._notified = self._notifier, None, None
        thread_watch, self._thread_watch = self._thread_watch, None
        if thread_watch is not None:
            thread_watch.close()
        if notified is not None and not notified.done():
            notified.cancel()
        if notified is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await notified

    async def run(self, step: Awaitable[_T], *, operation: str) -> _T:
        """Await ``step``; once the signal is set, cancel it and raise ``TransferCancelledError``."""
        if self.cancel is None:
            return await step
        task: asyncio.Task[_T] = asyncio.ensure_future(step)
        try:
            while True:
                notified = self._notified
                if notified is None:
                    await asyncio.wait({task}, timeout=POLL_SECONDS)
                else:
                    await _first_done(task, notified)
                if task.done():
                    return task.result()
                raise_if_transfer_cancelled(self.cancel, operation=operation)
                if notified is not None and notified.done():
                    # The notification fired but the signal reads unset (cleared
                    # again, or an asyncio.Event bound to another loop): check it
                    # by polling from here on.
                    self._notified = None
        except BaseException:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            raise
