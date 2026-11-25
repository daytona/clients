# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The server's retry hint of a failed read: the error body's
``retryAfterMs`` / ``retry_after_seconds``, else the response's
``Retry-After`` header (kept beside the error by the transport). The page
walks of ``iter()`` read it (:mod:`._pages`); the Go and TypeScript SDKs read
the same hint."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from weakref import WeakKeyDictionary

from .errors import DaytonaError

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
