# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Docker Engine status inside a sandbox (``sandbox.docker``).

The sandbox env variable ``DAYTONA_DOCKER_START`` chooses how Docker starts:
``auto`` (default; start the image's ``dockerd`` when it ships one),
``entrypoint`` (the image's own entrypoint starts Docker; only wait for it) or
``off`` (neither start nor wait; state ``disabled``). Unknown values count as
``auto``.
"""

from __future__ import annotations

import asyncio
import math
from typing import Final, Optional

from . import _clock
from ._core import WAIT_READ_SLACK_SECONDS, RequestSpec, expect_docker_status
from ._transport import AsyncTransport
from .errors import ClientTimeoutError, DockerNotReadyError, DockerWaitTimeoutError, create_error
from .types import DockerStatus

#: Default ``wait_until_ready()`` budget in seconds.
DOCKER_WAIT_DEFAULT_TIMEOUT_SECONDS: Final = 60.0
#: Longest server-side hold per status poll, in milliseconds; caps ``waitMs``.
DOCKER_STATUS_MAX_WAIT_MS: Final = 1000
#: Minimum spacing between polls answered without holding (e.g. ``deferred``).
DOCKER_STATUS_POLL_INTERVAL_SECONDS: Final = 0.5
#: How far past the caller's deadline one status request may run before the
#: transport abandons it, in seconds. Bounds the overshoot of a stalled request.
DOCKER_WAIT_DEADLINE_GRACE_SECONDS: Final = 0.25
#: States that cannot become ``ready`` in this boot. ``starting`` settles on its own;
#: ``deferred`` keeps polling.
DOCKER_TERMINAL_STATES: Final = frozenset({"failed", "not_present", "disabled", "unmanaged", "unsupported"})


class AsyncDockerService:
    """Read-only Docker Engine status. Nothing here starts or restarts Docker.

    A sandbox without Docker status support answers :meth:`status` and
    :meth:`wait_until_ready` with a ``NotFoundError`` (404).
    """

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def status(self) -> DockerStatus:
        """Return the current Docker Engine status for this boot, answered immediately.

        Raises ``NotFoundError`` (404) when the sandbox does not support the route.
        """
        return expect_docker_status(await self._transport.send(RequestSpec("GET", "/docker/status")))

    async def wait_until_ready(self, *, timeout: float = DOCKER_WAIT_DEFAULT_TIMEOUT_SECONDS) -> DockerStatus:
        """Poll the Docker status until it is ``ready`` and return that status.

        ``timeout`` is the wait budget in seconds (default 60); ``0`` waits
        without a deadline — avoid it for sandboxes whose Docker can stay
        ``deferred``. Each poll asks the server to hold for up to one second
        while Docker is ``starting``; answers that come back sooner are spaced
        0.5 s apart.

        Raises :class:`DockerNotReadyError` as soon as Docker settles in a
        state that cannot become ``ready`` (``failed``, ``not_present``,
        ``disabled``, ``unmanaged``, or ``unsupported``), and
        :class:`DockerWaitTimeoutError` when the budget runs out while Docker
        is still ``starting`` or ``deferred`` — including
        when a status request is still unanswered at the deadline. No request
        runs more than a fraction of a second past the deadline.
        """
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise create_error("timeout must be a non-negative finite number", 400, "BAD_REQUEST", "SDK")
        deadline = None if timeout == 0 else _clock.monotonic() + timeout
        last: Optional[DockerStatus] = None
        while True:
            remaining = None if deadline is None else deadline - _clock.monotonic()
            if remaining is not None and remaining <= 0:
                raise DockerWaitTimeoutError(last)
            wait_ms = (
                DOCKER_STATUS_MAX_WAIT_MS
                if remaining is None
                else min(DOCKER_STATUS_MAX_WAIT_MS, max(1, math.ceil(remaining * 1000)))
            )
            # The server answers within ``waitMs``; the slack only bounds a stalled
            # one, and never lets a request run far past the caller's deadline.
            request_timeout = wait_ms / 1000 + WAIT_READ_SLACK_SECONDS
            if remaining is not None:
                request_timeout = min(request_timeout, remaining + DOCKER_WAIT_DEADLINE_GRACE_SECONDS)
            poll_started = _clock.monotonic()
            try:
                payload = await self._transport.send(
                    RequestSpec("GET", "/docker/status", params={"waitMs": wait_ms}, timeout=request_timeout)
                )
            except ClientTimeoutError as exc:
                if deadline is not None and _clock.monotonic() >= deadline:
                    raise DockerWaitTimeoutError(last) from exc
                raise
            last = expect_docker_status(payload)
            state = last.state
            if state == "ready":
                return last
            if last.state in DOCKER_TERMINAL_STATES:
                raise DockerNotReadyError(last)
            # ``starting``, ``deferred``, or a state this SDK does not know yet: keep polling.
            elapsed = _clock.monotonic() - poll_started
            if elapsed < DOCKER_STATUS_POLL_INTERVAL_SECONDS:
                pause = DOCKER_STATUS_POLL_INTERVAL_SECONDS - elapsed
                if deadline is not None:
                    pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                if pause > 0:
                    await asyncio.sleep(pause)
