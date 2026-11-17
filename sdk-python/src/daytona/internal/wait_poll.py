# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Poll cadence and pre-request subscriptions of sandbox state waits, shared by the sync and async sandboxes."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

# Events are flowing, so the poll only guards against a missed event.
EVENT_BACKED_POLL_INTERVAL = 5.0
# Subscribed but the event connection is not up (connecting, reconnecting or failed): poll at the
# subscribed rate instead of the fast one, so a gateway outage does not multiply the poll load.
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


def poll_interval(subscribed: bool, connected: bool, fast_poll_interval: float) -> float:
    """Returns the delay before the next safety-net poll of a wait.

    Args:
        subscribed: Whether the wait holds a state event subscription.
        connected: Whether the event connection is up.
        fast_poll_interval: The current fast interval, used as is when not subscribed.
    """
    if not subscribed:
        return fast_poll_interval
    base = EVENT_BACKED_POLL_INTERVAL if connected else SUBSCRIBED_POLL_INTERVAL
    return base * (1 - POLL_JITTER + 2 * POLL_JITTER * random_unit())


def next_fast_poll_interval(fast_poll_interval: float) -> float:
    """Returns the fast interval after one more poll past FAST_POLL_PERIOD."""
    return min(fast_poll_interval * FAST_POLL_GROWTH, FAST_POLL_MAX_INTERVAL)


@dataclass(frozen=True)
class WaitSubscription:
    """A wait's state subscription taken before the operation's request is sent.

    Attributes:
        sub_id: Subscription id; empty when events are unavailable.
        live: Whether the event connection was up right after subscribing.
        state_event_count: The sandbox's state event count when the subscription was taken.
    """

    sub_id: str
    live: bool
    state_event_count: int
