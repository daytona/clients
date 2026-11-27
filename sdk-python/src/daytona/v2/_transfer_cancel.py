# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Race a file transfer's network waits against its cancel signal.

A transfer with no signal awaits the network directly. A plain
``threading.Event`` on CPython 3.10 to 3.15 wakes the transfer when it is
set, from any thread, with no periodic wake-ups; elsewhere it is polled.

Any other object with ``is_set()``, ``asyncio.Event`` included, is checked
every ``POLL_SECONDS`` while the transfer waits, and only then. The watch
never waits on an ``asyncio.Event``: that would bind the caller's event to
the transfer's loop, and its ``set()`` from another thread (unsafe for
asyncio, and an error in debug mode) could leave the waiter stuck.

The ``threading.Event`` watch briefly takes the event's lock on the thread
running the transfer, as ``Event.wait()`` does: a signal handler on that
thread must not call ``set()`` on the same event (use
``loop.add_signal_handler`` instead).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import sys
import threading
from types import TracebackType
from typing import AsyncIterator, Awaitable, Callable, Final, Optional, Type, TypeVar

from typing_extensions import Self

from ._shutdown import refusing_stopped_pools
from .errors import TransferCancelledError
from .types import TransferCancelSignal

_T = TypeVar("_T")

POLL_SECONDS: Final[float] = 0.05
# The newest CPython whose ``threading.Event`` / ``Condition`` internals the
# notification path was checked against (3.10 through 3.15). Later versions
# poll until someone checks them again.
_THREAD_EVENT_CHECKED_THROUGH: Final[tuple[int, int]] = (3, 15)


def raise_if_transfer_cancelled(cancel: Optional[TransferCancelSignal]) -> None:
    """Raise ``TransferCancelledError`` (its default message, the same in every
    SDK) once ``cancel`` is set."""
    if cancel is not None and cancel.is_set():
        raise TransferCancelledError()


async def _first_done(task: asyncio.Task[_T], notified: asyncio.Future[object]) -> None:
    """Return once ``task`` or ``notified`` is done."""
    loop = asyncio.get_running_loop()
    woken: asyncio.Future[None] = loop.create_future()

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


async def _settle(future: asyncio.Future[object]) -> None:
    """Wait for ``future`` to finish and consume its outcome.

    ``asyncio.wait`` raises neither the future's error nor its own
    cancellation, but a cancellation of the calling task still propagates.
    """
    if not future.done():
        await asyncio.wait({future})
    if not future.cancelled():
        future.exception()


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
        """The condition a plain ``threading.Event`` notifies, else None.

        ``threading.Event`` has no public notification hook, and a thread
        blocked in its ``wait()`` cannot be released when the transfer ends,
        so the watch queues a lock on the event's condition the way
        ``Condition.wait()`` does. An event built any other way gets None
        here and is polled.
        """
        if (
            type(event) is not threading.Event
            or sys.implementation.name != "cpython"
            or sys.version_info[:2] > _THREAD_EVENT_CHECKED_THROUGH
        ):
            return None
        condition = getattr(event, "_cond", None)
        if not isinstance(condition, threading.Condition):
            return None
        if not isinstance(getattr(condition, "_waiters", None), collections.deque):
            return None
        return condition

    def start(self, event: threading.Event) -> None:
        """Start watching ``event``; raises ``RuntimeError`` when no thread can start.

        The thread starts first, so a failure leaves nothing queued on the
        caller's event.
        """
        self._start_thread(self.thread)
        with self._condition:
            if event.is_set():
                self._wake.release()
            else:
                self._condition._waiters.append(self._wake)  # type: ignore[attr-defined]

    @staticmethod
    def _start_thread(thread: threading.Thread) -> None:
        thread.start()

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
        if self.thread.ident is not None:
            self.thread.join()


class TransferCancelWatch:
    """Watches one transfer's cancel signal for as long as the transfer runs.

    Use as ``async with TransferCancelWatch(cancel) as watch`` and await each
    network step through ``watch.run(step)``.
    """

    def __init__(self, cancel: Optional[TransferCancelSignal]) -> None:
        self.cancel = cancel
        self._notifier: Optional[asyncio.Future[object]] = None
        self._notified: Optional[asyncio.Future[object]] = None
        self._thread_watch: Optional[_ThreadEventWatch] = None

    @property
    def notifies(self) -> bool:
        """Whether setting the signal wakes a wait on the network."""
        return self._notified is not None

    @property
    def polling(self) -> bool:
        """Whether a wait on the network wakes every ``POLL_SECONDS`` to check the signal."""
        return self.cancel is not None and self._notified is None

    async def __aenter__(self) -> Self:
        cancel = self.cancel
        if cancel is None:
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
            try:
                watch.start(cancel)  # type: ignore[arg-type]
                self._thread_watch = watch
                self._notifier = self._notified = notified
            except RuntimeError:
                # No thread can start (a thread limit, interpreter shutdown):
                # the event is polled like any other is_set() object.
                return self
            except BaseException:
                # An interrupt before the watch is fully recorded: __aexit__
                # will not run, so release the helper here.
                self._thread_watch = self._notifier = self._notified = None
                watch.close()
                raise
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
        if notified is not None:
            notified.cancel()
            await _settle(notified)

    async def run(self, step: Awaitable[_T]) -> _T:
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
                raise_if_transfer_cancelled(self.cancel)
                if notified is not None and notified.done():
                    # The notification fired but the signal reads unset (set and
                    # cleared again): check it by polling from here on.
                    self._notified = None
        except BaseException:
            task.cancel()
            await _settle(task)
            raise


@contextlib.asynccontextmanager
async def transfer_scope(cancel: Optional[TransferCancelSignal]) -> AsyncIterator[TransferCancelWatch]:
    """One file transfer: its cancel watch, with a stopped thread pool at
    interpreter shutdown (the transfer's file I/O runs on one) raised as the
    shutdown ``ClientClosedError``, as the transport's requests are."""
    with refusing_stopped_pools():
        async with TransferCancelWatch(cancel) as watch:
            yield watch
