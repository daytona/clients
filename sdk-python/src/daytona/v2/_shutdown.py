# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Refuse requests made while the interpreter shuts down.

``atexit`` handlers and finalizers run after Python has stopped the thread
pools a request needs (a DNS lookup, a new connection, the synchronous
client's event loop thread): a request made there fails at random, with an
untyped ``RuntimeError``, or succeeds only when an old connection happens to
be reusable. The SDK refuses such a request before sending it, with a
``ClientClosedError`` that names the supported alternative.

Shutdown is detected on the main thread, where ``atexit`` handlers run, once
it has finished the program (``threading.main_thread().is_alive()`` is false
from then on), and anywhere once the interpreter is finalizing. Another
thread still running after the program ended keeps working; when one of its
requests fails because a thread pool has stopped (or cannot be started any
more), the sync and async clients raise the same ``ClientClosedError``
instead of the bare ``RuntimeError``.

Python stops its thread pools in ``threading._shutdown()`` before it marks
the main thread finished, so a failure is also mapped once that has begun:
``threading._SHUTTING_DOWN`` or ``concurrent.futures.thread._shutdown`` is
set. Both are private, so they are read with a fallback; the error message
must match a stopped pool too.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from typing import Iterator, Optional

from .errors import ClientClosedError

SHUTDOWN_REFUSAL = (
    "Daytona cannot run requests while the Python interpreter is shutting down (an atexit "
    "handler, a finalizer, a thread still running after the program ended): the threads a "
    "request needs have stopped. Delete or stop sandboxes before the program exits, for "
    "example in a try/finally block around the code that uses them."
)
# What the standard library raises when a request needs a thread pool or a new
# thread after shutdown began.
_STOPPED_POOL_MESSAGES = (
    "cannot schedule new futures after",
    "can't create new thread at interpreter shutdown",
    # The first thread pool of the process, created after shutdown began.
    "can't register atexit after shutdown",
)


def interpreter_shutting_down() -> bool:
    if sys.is_finalizing():
        return True
    main = threading.main_thread()
    return threading.current_thread() is main and not main.is_alive()


def refuse_during_interpreter_shutdown() -> None:
    if interpreter_shutting_down():
        raise ClientClosedError(SHUTDOWN_REFUSAL)


def _shutdown_began() -> bool:
    if sys.is_finalizing() or not threading.main_thread().is_alive():
        return True
    if getattr(threading, "_SHUTTING_DOWN", False) is True:
        return True
    pools = sys.modules.get("concurrent.futures.thread")
    return getattr(pools, "_shutdown", False) is True


def shutdown_refusal_for(error: RuntimeError) -> Optional[ClientClosedError]:
    """The refusal to raise for ``error``, when shutdown stopped a pool it needed; else None."""
    if not any(message in str(error) for message in _STOPPED_POOL_MESSAGES):
        return None
    if not _shutdown_began():
        return None
    return ClientClosedError(SHUTDOWN_REFUSAL)


@contextlib.contextmanager
def refusing_stopped_pools() -> Iterator[None]:
    """Raise :func:`shutdown_refusal_for`'s error in place of a stopped pool's ``RuntimeError``."""
    try:
        yield
    except RuntimeError as error:
        refusal = shutdown_refusal_for(error)
        if refusal is None:
            raise
        raise refusal from error
