# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Runs the async SDK from synchronous callers.

The SDK is authored once, async. The synchronous surface is this bridge plus
generated pass-through wrappers: a dedicated event loop on a background thread,
onto which every blocking call is dispatched. Nothing here sits on the async
path — async callers run on their own loop and never import this module.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import contextlib
import os
import threading
import warnings
import weakref
from contextlib import AbstractContextManager
from types import TracebackType
from typing import AsyncIterator, Awaitable, Callable, Iterator, Optional, TypeVar

import anyio
from anyio.from_thread import BlockingPortal, start_blocking_portal
from typing_extensions import Self

from .errors import DaytonaError, _interrupt_error_of

T = TypeVar("T")

_IN_EVENT_LOOP = (
    "{name} is the synchronous client and cannot be called from a running event loop; "
    "it would block that loop until the request completed. Use the async client instead: {async_name}."
)

_AFTER_FORK = (
    "this synchronous client was created in process {owner} and is being used in forked child "
    "{current}; its event loop and open connections did not survive the fork. Construct a new "
    "client inside the child process."
)


class BridgeError(DaytonaError):
    """Misuse of the synchronous surface itself, raised before any request is sent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, source="SDK")


def _in_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class SyncBridge:
    """Owns one event loop thread and dispatches coroutines onto it.

    One bridge per synchronous root client. The thread starts on the first call
    rather than at construction, so building a client that is never used costs
    no thread.
    """

    def __init__(self, *, name: str = "Daytona", on_exit: Callable[[], None] | None = None) -> None:
        self._name = name
        # Run at interpreter exit for a bridge the program never closed —
        # the root client passes its own close() so HTTP pools are released
        # before the loop thread stops, exactly as an explicit close() does.
        self._on_exit = on_exit
        self._async_name = f"Async{name}" if name else "the async client"
        self._lock = threading.Lock()
        self._context: Optional[AbstractContextManager[BlockingPortal]] = None
        self._portal: Optional[BlockingPortal] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # the portal's loop, once started
        self._closing_task: Optional[asyncio.Task[object]] = None  # the in-loop call that invoked close()
        self._inflight = 0
        self._inflight_lock = threading.Lock()  # never held while waiting on the loop
        self._idle = threading.Condition(self._inflight_lock)
        self._owner_pid = os.getpid()
        self._closed = False
        with _LIVE_BRIDGES_LOCK:
            _LIVE_BRIDGES.add(self)

    def _portal_locked(self) -> BlockingPortal:
        current_pid = os.getpid()
        if current_pid != self._owner_pid:
            raise BridgeError(_AFTER_FORK.format(owner=self._owner_pid, current=current_pid))
        if self._closed:
            raise BridgeError(f"{self._name} is closed")
        if self._portal is None:
            context = start_blocking_portal()
            portal = context.__enter__()
            self._context = context
            self._portal = portal

            async def capture_loop() -> asyncio.AbstractEventLoop:
                return asyncio.get_running_loop()

            self._loop = portal.call(capture_loop)
        return self._portal

    @property
    def closed(self) -> bool:
        return self._closed

    def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run ``operation()`` on the bridge loop and block for its result.

        Takes a zero-argument callable rather than ``(func, *args)`` so the
        coroutine is built by the caller and stays fully typed.
        """
        if _in_running_loop():
            raise BridgeError(_IN_EVENT_LOOP.format(name=self._name, async_name=self._async_name))
        unwound = threading.Event()
        # A typed outcome the cancelled operation attached to its cancellation
        # (see errors._attach_interrupt_error); written on the loop before
        # `unwound` is set, read here only after waiting on it.
        interrupt_errors: list[DaytonaError] = []

        async def run_and_signal() -> T:
            # In-flight accounting lives HERE, on the loop, so it is ordered
            # with the work itself: counting on the submitting thread races the
            # coroutine's first step (the loop can run it to its first await
            # before start_task_soon() has even returned to the caller).
            #
            # It uses its OWN lock: the loop thread must never take
            # self._lock, which a submitting caller holds while it waits on
            # the loop to accept the task — that pairing deadlocks.
            with self._inflight_lock:
                self._inflight += 1
            try:
                return await operation()
            except BaseException as exc:
                interrupt_error = _interrupt_error_of(exc)
                if interrupt_error is not None:
                    interrupt_errors.append(interrupt_error)
                raise
            finally:
                with self._inflight_lock:
                    self._inflight -= 1
                    if self._inflight == 0:
                        self._idle.notify_all()
                unwound.set()

        # Submission happens under the lifecycle lock so a concurrent close()
        # cannot stop the portal between acquiring it and handing it the task.
        # Only submission is serialised — awaiting the result is not, or
        # concurrent callers would run one at a time.
        future: concurrent.futures.Future[T] | None = None
        try:
            with self._lock:
                future = self._portal_locked().start_task_soon(run_and_signal)
            return future.result()
        except KeyboardInterrupt as interrupt:
            if future is None:
                raise  # interrupted before anything was submitted; nothing to cancel
            # Ctrl-C lands on this thread while the work runs on the loop
            # thread, where it would keep going — holding its connection —
            # once we unwind. Cancel it, then wait for the task to actually
            # terminate so we never return while it is still running.
            #
            # This bounds the task's lifetime, NOT the completeness of its
            # cleanup: cancellation propagates into the operation, so any
            # `finally` that awaits is itself cancelled partway. Teardown that
            # must survive an interrupt shields itself at the point of use —
            # see AsyncResponseStream.close().
            #
            # The wait is on our own event, not on the future: when a cancel
            # scope absorbs the CancelledError, anyio leaves the future in
            # CANCELLED without ever reaching CANCELLED_AND_NOTIFIED, so
            # concurrent.futures.wait() on it never returns early and would
            # turn every Ctrl-C into a full settle-timeout stall.
            future.cancel()
            if not unwound.wait(_CANCEL_SETTLE_SECONDS):
                warnings.warn(
                    f"{self._name}: a cancelled operation did not finish unwinding within "
                    f"{_CANCEL_SETTLE_SECONDS}s and is still holding its resources; "
                    f"close() will block until it finishes.",
                    RuntimeWarning,
                    stacklevel=2,
                )
            elif interrupt_errors:
                # The operation turned its cancellation into a typed outcome
                # (e.g. a build-log follower's last checkpoint). It has fully
                # unwound, so surface that instead of the bare interrupt.
                raise interrupt_errors[0] from interrupt
            raise

    def call_sync(self, operation: Callable[[], T]) -> T:
        """Run a plain callable on the bridge loop and block for its result.

        For methods that are not coroutines but touch loop-owned state (an
        ``asyncio.Queue``, a registry other coroutines mutate): running them
        on the loop keeps that state single-threaded.
        """

        if self._on_own_loop():
            # Already on the loop (a callback the SDK runs): the state is
            # single-threaded here, so read it directly.
            return operation()

        async def on_loop() -> T:
            return operation()

        return self.call(on_loop)

    def post(self, operation: Callable[[], object]) -> None:
        """Schedule a plain callable on the bridge loop and return at once.

        The caller's thread keeps running (and normally keeps the GIL) while
        the loop picks it up: a watch's synchronous twin posts the release of
        its callbacks this way, so the caller has usually stored the returned
        handle before the first callback runs. Runs it at once when called on
        the loop itself. A post after ``close()`` raises ``BridgeError``, as
        every call does, whichever thread makes it.
        """
        if self._on_own_loop():
            # The loop thread never takes self._lock (see call()): a caller
            # holding it may be waiting for this loop. close() sets _closed
            # under the lock, and reading one bool needs none. A close() that
            # starts after this check has not stopped the loop yet, so the
            # operation runs as a post that won the race would.
            if self._closed:
                raise BridgeError(f"{self._name} is closed")
            operation()
            return
        # Scheduled under the lock close() takes before it stops the loop
        # (call_soon_threadsafe does not block), so a post() either lands on
        # a running loop or finds the bridge closed.
        with self._lock:
            self._portal_locked()
            loop = self._loop
            assert loop is not None  # set when the portal started
            loop.call_soon_threadsafe(operation)

    def on_loop_thread(self) -> bool:
        """Whether the caller runs on this bridge's own event-loop thread, as
        the callbacks the SDK invokes (a watch's ``on_event``) do."""
        return self._on_own_loop()

    def iterate(self, agen: AsyncIterator[T]) -> Iterator[T]:
        """Drain an async iterator from synchronous code.

        Leaving this generator early — ``break``, an exception, or garbage
        collection — closes the underlying async iterator on the loop thread, so
        the stream and its connection are released instead of parked until the
        server gives up.
        """
        try:
            while True:
                try:
                    yield self.call(agen.__anext__)
                except StopAsyncIteration:
                    return
        finally:
            self._aclose_quietly(agen)

    def _aclose_quietly(self, agen: AsyncIterator[T]) -> None:
        aclose = getattr(agen, "aclose", None)
        if aclose is None or self._portal is None or self._closed:
            return
        try:
            self.call(aclose)
        except (BridgeError, StopAsyncIteration):
            pass

    def _closing_call_active(self) -> bool:
        task = self._closing_task
        return task is not None and not task.done()

    def _on_own_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def close(self, finalizer: Callable[[], Awaitable[None]] | None = None) -> None:
        """Run ``finalizer`` on the loop once in-flight calls have finished, then stop the loop.

        ``finalizer`` is the owner's own async teardown (the root client's
        HTTP pools). It runs after every previously submitted call has
        returned — a request still using a pool is never cut off by the pool
        closing under it — and before the loop stops. Idempotent.

        From an ordinary thread this blocks. From the bridge's OWN loop (a
        sync callback the loop is running calls ``client.close()``) it cannot
        block or join that thread: the drain + finalizer run as a task and the
        portal shutdown is handed to a helper thread that waits for them. A
        caller on some *other* running loop is treated as an ordinary thread
        — its loop is not ours to schedule on — except that it must not block
        it, so the same helper-thread path is used.

        A bridge whose loop never started still runs the finalizer, on a
        throwaway loop, so an owner that created resources eagerly but never
        made a call does not leak them.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True  # no new submissions from here on
            context, portal, own_loop = self._context, self._portal, self._loop
            self._context = self._portal = None
        if os.getpid() != self._owner_pid:
            return
        if context is None or portal is None:
            if finalizer is not None:

                def finalize_on_throwaway_loop() -> None:
                    # The coroutine is built inside asyncio.run, so a failure
                    # can never leave an un-awaited one behind.
                    with contextlib.suppress(Exception):
                        asyncio.run(_bounded(finalizer))

                if _in_running_loop():
                    # asyncio.run cannot nest in the caller's running loop, and
                    # that loop must not be blocked: same rule as below.
                    threading.Thread(
                        target=finalize_on_throwaway_loop, name="daytona-bridge-shutdown", daemon=True
                    ).start()
                else:
                    finalize_on_throwaway_loop()
            return

        def drain_then_finalize_on_loop() -> None:
            # runs on the caller's thread: wait for in-flight calls, then run the finalizer on the loop
            with self._inflight_lock:
                while self._inflight:
                    self._idle.wait(timeout=_CANCEL_SETTLE_SECONDS)
            if finalizer is not None:
                with contextlib.suppress(Exception):
                    portal.call(_bounded, finalizer)
            context.__exit__(None, None, None)

        if self._on_own_loop():
            done = threading.Event()
            self._closing_task = asyncio.current_task()  # excluded from the drain while it runs

            async def finalize_after_inflight() -> None:
                try:
                    # The call that invoked close() is itself in flight and will
                    # finish on its own; everything else must drain first. Wait
                    # for the count to reach the calls still running besides us —
                    # zero once our own call has returned (which it does as soon
                    # as close() hands off here), so "nothing but possibly me".
                    while self._inflight > (1 if self._closing_call_active() else 0):
                        await asyncio.sleep(0.01)
                    if finalizer is not None:
                        await _bounded(finalizer)
                finally:
                    done.set()

            own_loop.create_task(finalize_after_inflight())  # type: ignore[union-attr]

            def shut_down() -> None:
                # The drain has no fixed bound and the finalizer has its own
                # (_FINALIZER_GRACE_SECONDS); this wait must outlast both, or
                # the portal exit would cancel a finalizer that is still
                # entitled to run. Drain waits are bounded per call by the
                # settle window, so the sum is the ceiling.
                done.wait(_CANCEL_SETTLE_SECONDS + _FINALIZER_GRACE_SECONDS)
                context.__exit__(None, None, None)

            threading.Thread(target=shut_down, name="daytona-bridge-shutdown", daemon=True).start()
            return
        if _in_running_loop():
            # some other loop: do not block it; drain and finalize on a helper thread
            threading.Thread(
                target=drain_then_finalize_on_loop, name="daytona-bridge-shutdown", daemon=True
            ).start()
            return
        drain_then_finalize_on_loop()

    def _reset_after_fork(self) -> None:
        # Releasing the inherited portal here would be fatal: finalizing the
        # context manager runs its shutdown, which waits on an event loop whose
        # thread did not survive fork(). The child then deadlocks before its
        # first statement. Park the inherited objects somewhere permanent so
        # they are never finalized, and re-arm the lock, which may have been
        # held by a thread that no longer exists.
        if self._context is not None or self._portal is not None:
            _ORPHANED_BY_FORK.append((self._context, self._portal))
        self._lock = threading.Lock()
        self._inflight_lock = threading.Lock()
        self._idle = threading.Condition(self._inflight_lock)
        self._inflight = 0
        self._context = None
        self._portal = None
        self._loop = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.close()


_CANCEL_SETTLE_SECONDS = 5.0
_FINALIZER_GRACE_SECONDS = 10.0


async def _bounded(finalizer: Callable[[], Awaitable[None]]) -> None:
    """The owner's teardown, but never able to hold shutdown hostage."""
    with anyio.move_on_after(_FINALIZER_GRACE_SECONDS):
        await finalizer()


_LIVE_BRIDGES: weakref.WeakSet[SyncBridge] = weakref.WeakSet()
# Guards _LIVE_BRIDGES: a thread creating a client must not change the set
# while another copies it.
_LIVE_BRIDGES_LOCK = threading.Lock()


def _live_bridges() -> list[SyncBridge]:
    with _LIVE_BRIDGES_LOCK:
        return list(_LIVE_BRIDGES)


def on_a_bridge_loop() -> bool:
    """Whether the running coroutine runs on a synchronous client's bridge
    loop, where only a synchronous twin can have called it (a synchronous call
    made on that loop raises ``BridgeError``)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return any(bridge._loop is loop for bridge in _live_bridges())


_ORPHANED_BY_FORK: list[object] = []


def _reset_live_bridges_after_fork() -> None:
    # The child has one thread, but may have inherited the lock held by a
    # thread that no longer exists: replace it before taking it.
    global _LIVE_BRIDGES_LOCK
    _LIVE_BRIDGES_LOCK = threading.Lock()
    for bridge in _live_bridges():
        bridge._reset_after_fork()


def _close_live_bridges_at_exit() -> None:
    # A bridge the program never closed must not hang interpreter exit. Left
    # alone, Python finalizes the un-exited portal context manager during
    # shutdown, and its stop() blocks on a loop thread that is already being
    # torn down — forever. Closing here, while the loop thread is still fully
    # alive, is the ordinary orderly shutdown; close() is idempotent, so a
    # program that did close pays nothing.
    for bridge in _live_bridges():
        if bridge._closed:
            continue
        with contextlib.suppress(Exception):
            if bridge._on_exit is not None:
                bridge._on_exit()
            else:
                bridge.close()


atexit.register(_close_live_bridges_at_exit)

if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_live_bridges_after_fork)
