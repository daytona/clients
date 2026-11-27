# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Retry an artifact submission only while the sandbox is transitioning."""

from typing import Final

from anyio import sleep

from . import _clock, _codes
from ._core import RequestSpec, TransportPayload
from ._transport import AsyncTransport
from .errors import DaytonaError

_RETRY_BUDGET_SECONDS: Final = 30.0


async def send_with_transition_retry(api: AsyncTransport, spec: RequestSpec) -> tuple[TransportPayload, bool]:
    """Admit retries for 30 seconds without shortening an in-flight request."""
    deadline = _clock.monotonic() + _RETRY_BUDGET_SECONDS
    retried = False
    while True:
        try:
            return await api.send(spec), retried
        except DaytonaError as error:
            if error.code != _codes.SANDBOX_TRANSITION_IN_PROGRESS:
                raise
            delay = min(5, max(1, error.retry_after_seconds or 1))
            # A retry the window cannot admit after the full pause is not worth
            # waiting for: the refusal raises at once.
            if deadline - _clock.monotonic() <= delay:
                raise
            await sleep(delay)
            # A late wake-up past the window admits no further attempt.
            if _clock.monotonic() >= deadline:
                raise
            retried = True
