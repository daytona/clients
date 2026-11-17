# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Poll cadence and pre-request subscriptions of sandbox state waits, shared by the sync and async sandboxes."""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Callable

# Events are flowing, so the poll only guards against a missed event.
EVENT_BACKED_POLL_INTERVAL = 5.0
# Subscribed but not event-backed (the connection is connecting, reconnecting or failed, or the id is
# past the connection's subscription cap): poll at the subscribed rate instead of the fast one, so a
# gateway outage does not multiply the poll load.
SUBSCRIBED_POLL_INTERVAL = 1.0
# Spreads the polls of many concurrent waits.
POLL_JITTER = 0.2
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


def poll_interval(subscribed: bool, event_backed: bool, fast_poll_interval: float) -> float:
    """Returns the delay before the next safety-net poll of a wait.

    Args:
        subscribed: Whether the wait holds a state event subscription.
        event_backed: Whether events for the id arrive: connected, and the server holds the id.
        fast_poll_interval: The current fast interval, used as is when not subscribed.
    """
    if not subscribed:
        return fast_poll_interval
    base = EVENT_BACKED_POLL_INTERVAL if event_backed else SUBSCRIBED_POLL_INTERVAL
    return base * (1 - POLL_JITTER + 2 * POLL_JITTER * random_unit())


def next_fast_poll_interval(fast_poll_interval: float) -> float:
    """Returns the fast interval after one more poll past FAST_POLL_PERIOD."""
    return min(fast_poll_interval * FAST_POLL_GROWTH, FAST_POLL_MAX_INTERVAL)


@dataclass(frozen=True)
class WaitSubscription:
    """A wait's state subscription taken before the operation's request is sent.

    Attributes:
        sub_id: Subscription id; empty when events are unavailable.
        live: Whether events for the id arrived right after subscribing (connected and served).
        state_event_count: The sandbox's state event count when the subscription was taken.
        connection_epoch: The event connection's epoch when the subscription was taken.
        state_fresh: Whether the cached state was set after subscribing, from the operation's
            response or a successful read.
    """

    sub_id: str
    live: bool
    state_event_count: int
    connection_epoch: int
    state_fresh: bool = False

    def with_state_fresh(self, state_fresh: bool) -> WaitSubscription:
        return replace(self, state_fresh=state_fresh)
