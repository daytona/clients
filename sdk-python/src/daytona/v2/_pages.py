# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The page walk behind every ``iter()``: bounded retries of a page read and
the failed page's cursor on the error, the same policy as the TypeScript and
Go SDKs.

Unlike a wait's reads, a walk has no overall deadline, so it retries only
responses that say "try again" (no response, HTTP 429 other than
``LIMIT_EXCEEDED``, 502, 503, 504), at most three reads of a page: a 500 is
not a transient gateway status, and retrying the client's own request timeout
would multiply the bound the caller configured for each read."""

from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator, Awaitable, Callable, Final, Optional, TypeVar

from ._read_retry import _HEADER_HINTS, retry_after_seconds
from .errors import ConnectionError, DaytonaError, LimitExceededError
from .types import ListResult

_T = TypeVar("_T")

# Every read of one page, the first included.
PAGE_READ_ATTEMPTS: Final[int] = 3
# The pause before the second and the third read (each 80-100 % of it).
PAGE_READ_DELAYS_SECONDS: Final[tuple[float, ...]] = (0.5, 1.0)
# A server retry hint longer than this is not waited for: the error surfaces.
PAGE_READ_MAX_DELAY_SECONDS: Final[float] = 10.0
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 502, 503, 504})


async def _sleep(seconds: float) -> None:
    # A seam so tests never patch asyncio's own sleep.
    await asyncio.sleep(seconds)


def page_retry_delay(error: DaytonaError, retry: int) -> Optional[float]:
    """The pause before read ``retry + 2`` of a page after ``error``, or
    ``None`` when the error is not retried: only a dropped connection and
    HTTP 429 / 502 / 503 / 504 are (not an organization limit, and not the
    client's own request timeout, which already bounds each read). A server
    retry hint (body or ``Retry-After`` header) above
    :data:`PAGE_READ_MAX_DELAY_SECONDS` ends the walk without a retry."""
    if retry + 1 >= PAGE_READ_ATTEMPTS:
        return None
    if isinstance(error, LimitExceededError):
        return None
    if not isinstance(error, ConnectionError) and error.status_code not in _RETRYABLE_STATUSES:
        return None
    hint = retry_after_seconds(error)
    if hint > PAGE_READ_MAX_DELAY_SECONDS:
        return None
    return max(PAGE_READ_DELAYS_SECONDS[retry] * random.uniform(0.8, 1.0), hint)


def _with_page_cursor(error: DaytonaError, cursor: Optional[str]) -> DaytonaError:
    """A copy of ``error`` (same class, args and fields) carrying
    ``page_cursor``. The caught error itself is never changed: concurrent
    walks could receive one shared instance, as they can in the TypeScript
    and Go SDKs. Built without calling the class's constructor, whose
    signature differs between error classes."""
    copied = type(error).__new__(type(error), *error.args)
    copied.__dict__.update(error.__dict__)
    copied.page_cursor = cursor
    # The Retry-After header hint is kept beside the error, keyed by instance.
    hint = _HEADER_HINTS.get(error)
    if hint is not None:
        _HEADER_HINTS[copied] = hint
    return copied


async def read_page(
    fetch_page: Callable[[Optional[str]], Awaitable[ListResult[_T]]], cursor: Optional[str]
) -> ListResult[_T]:
    """One page, read again after a transient failure (see
    :func:`page_retry_delay`); the error that ends it is a copy carrying
    ``page_cursor=cursor``, the cursor to resume the walk with."""
    retry = 0
    while True:
        try:
            return await fetch_page(cursor)
        except DaytonaError as error:
            delay = page_retry_delay(error, retry)
            if delay is None:
                copied = _with_page_cursor(error, cursor).with_traceback(error.__traceback__)
                if error.__suppress_context__:
                    # An explicit ``from X`` (or ``from None``) on the original.
                    raise copied from error.__cause__
                # Implicitly chained to the original, which keeps its own context.
                raise copied
        retry += 1
        await _sleep(delay)


async def iter_pages(
    fetch_page: Callable[[Optional[str]], Awaitable[ListResult[_T]]], cursor: Optional[str] = None
) -> AsyncIterator[_T]:
    """Every item of every page from ``cursor`` on, guarding against a cursor
    cycle. An empty or repeated next cursor ends the walk."""
    seen_cursors: set[str] = set()
    if cursor is not None:
        seen_cursors.add(cursor)
    current_cursor = cursor
    while True:
        page = await read_page(fetch_page, current_cursor)
        for item in page.items:
            yield item
        next_cursor = page.next_cursor
        # An empty cursor ends the list too, as in TypeScript.
        if not next_cursor or next_cursor == current_cursor or next_cursor in seen_cursors:
            return
        seen_cursors.add(next_cursor)
        current_cursor = next_cursor
