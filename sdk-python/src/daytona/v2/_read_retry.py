# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Retries of a wait's failed observation reads.

A wait (sandbox state, Docker status, image or snapshot readiness) reads the
server until a target or terminal state, within one deadline. A read that
fails transiently (no response, the client's own read timer, HTTP 429, a 5xx
other than 501) is retried after a back-off of 0.2 s doubling to 5 s, or the
server's retry hint when longer, never past the wait's deadline. A spent
deadline raises the wait's own timeout with the last failed read as its cause.
Any other failure (a 4xx, a 501, ``LIMIT_EXCEEDED``) is raised at once. Only
reads are retried, never a request that changes something. The Go and
TypeScript SDKs apply the same policy.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, Final, Optional
from weakref import WeakKeyDictionary

import anyio

from . import _clock
from .errors import ClientTimeoutError, ConnectionError, DaytonaError, RequestTimeoutError

READ_RETRY_MIN_DELAY_S: Final = 0.2
READ_RETRY_MAX_DELAY_S: Final = 5.0

# Retry-After header values of error responses, kept beside the error: the
# public fields carry only what the body says.
_HEADER_HINTS: WeakKeyDictionary[DaytonaError, float] = WeakKeyDictionary()


def remember_retry_after(error: DaytonaError, header: Optional[str]) -> None:
    """Keep the seconds a ``Retry-After`` header asks for (delta seconds or an HTTP date)."""
    if not header:
        return
    value = header.strip()
    seconds: Optional[float] = None
    if value.isascii() and value.isdigit():
        # A value too large for a float reads as infinite and is ignored, as
        # in the Go and TypeScript SDKs.
        seconds = float(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    if math.isfinite(seconds):
        _HEADER_HINTS[error] = seconds


def _hint(value: Optional[int], unit: float) -> Optional[float]:
    """A body hint in seconds; a negative one, or one too large for a float, is none."""
    if value is None or value < 0:
        return None
    try:
        seconds = value * unit
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) else None


def retry_after_seconds(error: DaytonaError) -> float:
    """The server's retry hint: the body's ``retryAfterMs``, else its
    ``retry_after_seconds``, else the ``Retry-After`` header; 0 without one."""
    for hint in (_hint(error.retry_after_ms, 0.001), _hint(error.retry_after_seconds, 1.0)):
        if hint is not None:
            return hint
    return _HEADER_HINTS.get(error, 0.0)


def retryable_read_error(error: BaseException) -> bool:
    """Whether a failed observation read may be retried within the wait."""
    if isinstance(error, (ClientTimeoutError, ConnectionError)):
        return True
    if not isinstance(error, DaytonaError) or error.status_code is None:
        return False
    if error.status_code == 429:
        return error.code != "LIMIT_EXCEEDED"
    return 500 <= error.status_code < 600 and error.status_code != 501


class ReadRetry:
    """The back-off and the last failure of one wait's reads.

    A successful read resets the back-off but keeps the last failure, which
    becomes the cause of the wait's timeout.
    """

    def __init__(self, transport: object = None) -> None:
        """``transport``: the wait's transport. Its ``retry_sleep``, when it
        has one, makes the client's close end a pause with
        :class:`ClientClosedError`."""
        self.last_error: Optional[DaytonaError] = None
        self._delay = 0.0
        self._next_read: Optional[float] = None
        sleep = getattr(transport, "retry_sleep", None)
        self._sleep: Callable[[float], Awaitable[None]] = sleep if callable(sleep) else anyio.sleep

    def note(self, error: BaseException) -> bool:
        """Record a failed read; ``False`` when it must be raised instead."""
        if not isinstance(error, DaytonaError) or not retryable_read_error(error):
            return False
        self.last_error = error
        self._delay = min(max(READ_RETRY_MIN_DELAY_S, self._delay * 2), READ_RETRY_MAX_DELAY_S)
        self._next_read = _clock.monotonic() + max(self._delay, retry_after_seconds(error))
        return True

    def probe_failed(self, error: BaseException) -> None:
        """Record a failed events-hub probe. A transient failure schedules the
        next read as :meth:`note` does; any other failure the hub keeps waiting
        through (its probes stop only on 401, 403 and 404) is kept as the
        timeout's cause too. The wait's own timeout is not a read failure."""
        if self.note(error) or not isinstance(error, DaytonaError):
            return
        if not (isinstance(error, RequestTimeoutError) and error.source == "SDK"):
            self.last_error = error

    def succeeded(self) -> None:
        self._delay = 0.0
        self._next_read = None

    async def pause(self, deadline: Optional[float], *, not_before: Optional[float] = None) -> None:
        """Sleep until the next read is due (and not before ``not_before``, a
        wait's own poll spacing), never past ``deadline``."""
        due = self._next_read
        if not_before is not None and due is not None:
            due = max(due, not_before)
        if due is None:
            return
        pause = due - _clock.monotonic()
        if deadline is not None:
            pause = min(pause, deadline - _clock.monotonic())
        if pause > 0:
            await self._sleep(pause)
