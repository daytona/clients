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
from then on), and anywhere once the interpreter is finalizing. A non-daemon
thread the interpreter is still waiting for keeps working; when one of its
requests fails because a thread pool has stopped, that failure gets the same
``ClientClosedError`` instead of the bare ``RuntimeError``.
"""

from __future__ import annotations

import sys
import threading
from typing import Optional

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
)


def interpreter_shutting_down() -> bool:
    if sys.is_finalizing():
        return True
    main = threading.main_thread()
    return threading.current_thread() is main and not main.is_alive()


def refuse_during_interpreter_shutdown() -> None:
    if interpreter_shutting_down():
        raise ClientClosedError(SHUTDOWN_REFUSAL)


def shutdown_refusal_for(error: RuntimeError) -> Optional[ClientClosedError]:
    """The refusal to raise for ``error``, when shutdown stopped a pool it needed; else None."""
    if not (sys.is_finalizing() or not threading.main_thread().is_alive()):
        return None
    if not any(message in str(error) for message in _STOPPED_POOL_MESSAGES):
        return None
    return ClientClosedError(SHUTDOWN_REFUSAL)
