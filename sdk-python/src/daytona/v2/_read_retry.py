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
from typing import Final, Optional
from weakref import WeakKeyDictionary

import anyio

from . import _clock
from .errors import ClientTimeoutError, ConnectionError, DaytonaError

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
    if value.isdigit():
        seconds = float(int(value))
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


def retry_after_seconds(error: DaytonaError) -> float:
    """The server's retry hint: the body's ``retryAfterMs``, else its
    ``retry_after_seconds``, else the ``Retry-After`` header; 0 without one."""
    if error.retry_after_ms is not None:
        return error.retry_after_ms / 1000
    if error.retry_after_seconds is not None:
        return float(error.retry_after_seconds)
    return _HEADER_HINTS.get(error, 0.0)


def retryable_read_error(error: BaseException) -> bool:
    """Whether a failed observation read may be retried within the wait."""
    if isinstance(error, (ClientTimeoutError, ConnectionError)):
        return True
    if not isinstance(error, DaytonaError) or error.status_code is None:
        return False
    if error.status_code == 429:
        return error.code != "LIMIT_EXCEEDED"
    return error.status_code >= 500 and error.status_code != 501


class ReadRetry:
    """The back-off and the last failure of one wait's reads.

    A successful read resets the back-off but keeps the last failure, which
    becomes the cause of the wait's timeout.
    """

    def __init__(self) -> None:
        self.last_error: Optional[DaytonaError] = None
        self._delay = 0.0
        self._next_read: Optional[float] = None

    def note(self, error: BaseException) -> bool:
        """Record a failed read; ``False`` when it must be raised instead."""
        if not isinstance(error, DaytonaError) or not retryable_read_error(error):
            return False
        self.last_error = error
        self._delay = min(max(READ_RETRY_MIN_DELAY_S, self._delay * 2), READ_RETRY_MAX_DELAY_S)
        self._next_read = _clock.monotonic() + max(self._delay, retry_after_seconds(error))
        return True

    def remember(self, error: BaseException) -> None:
        """Keep a retryable failure as the timeout cause without scheduling a read
        (an events-hub probe, which the hub reschedules itself)."""
        if isinstance(error, DaytonaError) and retryable_read_error(error):
            self.last_error = error

    def succeeded(self) -> None:
        self._delay = 0.0
        self._next_read = None

    async def pause(self, deadline: Optional[float]) -> None:
        """Sleep until the next read is due, never past ``deadline``."""
        if self._next_read is None:
            return
        pause = self._next_read - _clock.monotonic()
        if deadline is not None:
            pause = min(pause, deadline - _clock.monotonic())
        if pause > 0:
            await anyio.sleep(pause)
