# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Poll cadence and pre-request subscriptions of sandbox state waits, shared by the sync and async sandboxes."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

# Events are flowing, so the poll only guards against a missed event.
EVENT_BACKED_POLL_INTERVAL = 5.0
# Subscribed but not event-backed (the connection is connecting, reconnecting or failed, or the server
# rejected the id or it is past the connection's subscription limit): poll from 1 s, backing off x1.5
# per poll up to 10 s, so many such waits do not spend the request budget or run into rate limits.
FALLBACK_POLL_INTERVAL = 1.0
FALLBACK_POLL_GROWTH = 1.5
FALLBACK_POLL_MAX_INTERVAL = 10.0
# Spreads the polls of many concurrent subscribed waits; the fast cadence has none.
POLL_JITTER = 0.2
# A Retry-After longer than this is shortened to it; a wait's own timeout still ends the wait first.
MAX_RETRY_AFTER = 60.0
# Without a subscription the wait polls fast at first, then backs off up to FAST_POLL_MAX_INTERVAL
# after FAST_POLL_PERIOD.
FAST_POLL_INTERVAL = 0.1
FAST_POLL_PERIOD = 5.0
FAST_POLL_GROWTH = 1.1
FAST_POLL_MAX_INTERVAL = 1.0

# Uniform source in [0, 1] for the jitter; replaced in tests.
random_unit: Callable[[], float] = random.random


def carries_state(sandbox_dto: object | None) -> bool:
    """Whether an operation's response is a sandbox with a state, which can stand in for a read.

    A response without one is treated as an empty body: the caller falls back to a read.
    """
    return sandbox_dto is not None and bool(getattr(sandbox_dto, "state", None))


def poll_interval(
    subscribed: bool,
    event_backed: bool,
    fast_poll_interval: float,
    fallback_poll_interval: float = FALLBACK_POLL_INTERVAL,
) -> float:
    """Returns the delay before the next safety-net poll of a wait.

    Args:
        subscribed: Whether the wait holds a state event subscription.
        event_backed: Whether events for the id arrive: connected, and the server holds the id.
        fast_poll_interval: The current fast interval, used as is when not subscribed.
        fallback_poll_interval: The current fallback interval, used when subscribed but not event-backed.
    """
    if not subscribed:
        return fast_poll_interval
    base = EVENT_BACKED_POLL_INTERVAL if event_backed else fallback_poll_interval
    return base * (1 - POLL_JITTER + 2 * POLL_JITTER * random_unit())


def next_fallback_poll_interval(fallback_poll_interval: float) -> float:
    """Returns the fallback interval after one more poll that was not event-backed (or was rate limited)."""
    return min(fallback_poll_interval * FALLBACK_POLL_GROWTH, FALLBACK_POLL_MAX_INTERVAL)


def retry_after_seconds(headers: Mapping[str, Any] | None, now: datetime | None = None) -> float | None:
    """Returns the delay a rate-limited response asks for in ``Retry-After``, at most MAX_RETRY_AFTER.

    Returns None when the header is missing or unusable: unparseable, negative or not finite.

    Args:
        headers: The response headers; the name is matched case-insensitively.
        now: The current time, for an HTTP-date value; defaults to the system clock.
    """
    value = next((v for k, v in (headers or {}).items() if str(k).lower() == "retry-after"), None)
    if value is None:
        return None
    text = str(value).strip()
    # Delay-seconds is a whole number (RFC 9110); anything else that isn't an HTTP date is unusable.
    if text.isascii() and text.isdigit():
        # Long digit strings are far past the cap; skip converting them.
        seconds = float(int(text)) if len(text) <= 6 else MAX_RETRY_AFTER
    else:
        try:
            retry_at = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        # A date already past asks for no extra delay.
        seconds = max(0.0, (retry_at - (now or datetime.now(timezone.utc))).total_seconds())
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER)


def rate_limited_poll_interval(mode_interval: float, fallback_poll_interval: float, retry_after: float | None) -> float:
    """Returns the delay after a rate-limited poll, in any mode.

    The longest of the current mode's interval, the fallback backoff (with jitter) and Retry-After, so
    repeated rate limits back off even without a subscription. When Retry-After decides, it gets upward
    jitter only, so waits that got the same header do not retry together and none retries early.

    Args:
        mode_interval: The interval the wait's current mode chose, jitter included.
        fallback_poll_interval: The fallback backoff before this rate limit advances it.
        retry_after: The (capped) Retry-After delay, or None without one.
    """
    interval = max(mode_interval, fallback_poll_interval * (1 - POLL_JITTER + 2 * POLL_JITTER * random_unit()))
    if retry_after is not None and retry_after > interval:
        return retry_after * (1 + POLL_JITTER * random_unit())
    return interval


@dataclass(frozen=True)
class RateLimited:
    """A wait's poll was rate limited (HTTP 429).

    Attributes:
        retry_after: The delay Retry-After asks for (capped), or None without a usable header.
    """

    retry_after: float | None


def next_fast_poll_interval(fast_poll_interval: float) -> float:
    """Returns the fast interval after one more poll past FAST_POLL_PERIOD."""
    return min(fast_poll_interval * FAST_POLL_GROWTH, FAST_POLL_MAX_INTERVAL)


@dataclass(frozen=True)
class WaitSubscription:
    """A wait's state subscription, taken before the operation's request is sent (right after the
    response for a fork, whose id is only known then).

    Attributes:
        sub_id: Subscription id; empty when events are unavailable.
        live: Whether events for the id arrived right after subscribing (connected and served).
        state_event_count: The sandbox's state event count when the subscription was taken.
        connection_epoch: The event connection's epoch when the subscription was taken.
        state_fresh: Whether the cached state can stand in for the wait's first read: it came from the
            operation's response or a successful read after subscribing, or, for a fork, from the fork
            response with no extra round trip before the subscription.
    """

    sub_id: str
    live: bool
    state_event_count: int
    connection_epoch: int
    state_fresh: bool = False

    def with_state_fresh(self, state_fresh: bool) -> WaitSubscription:
        return replace(self, state_fresh=state_fresh)
