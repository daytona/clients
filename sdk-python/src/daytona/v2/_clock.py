# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The SDK's own monotonic clock.

Deadlines and back-off read ``monotonic()`` from here rather than ``time``
directly, so tests can drive a deterministic clock without also feeding
asyncio's event loop, which reads ``time.monotonic`` for its own scheduling.
"""

from __future__ import annotations

import time


def monotonic() -> float:
    return time.monotonic()
