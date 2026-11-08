# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Registry import submission and readiness under one cancellable deadline."""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import quote

import anyio

from . import _clock
from ._api_core import expect_create_image_resp, expect_image
from ._core import RequestSpec, TransportPayload, timeout_millis_from_seconds
from ._transport import AsyncTransport
from .errors import ClientTimeoutError, ImageBuildFailedError, RequestTimeoutError, create_error
from .types import Image, JsonObject

_POLL_MIN_INTERVAL_S = 1.0


@dataclass(frozen=True, slots=True)
class ImagePull:
    transport: AsyncTransport
    timeout: float | None

    async def run(self, submission: RequestSpec, *, wait: bool) -> Image:
        timeout_millis_from_seconds(self.timeout)
        budget = self.timeout or None
        deadline = None if budget is None else _clock.monotonic() + budget
        with anyio.move_on_after(self._remaining(deadline)) as scope:
            try:
                created = expect_create_image_resp(await self._send(submission, deadline))
                path = f"/images/{quote(created.id, safe='')}"
                image = expect_image(await self._send(RequestSpec("GET", path), deadline))
                last_poll_started: float | None = None
                while wait and image.status not in ("ready", "failed", "error"):
                    # An early non-terminal answer pauses up to a second before
                    # the next hold, as get(wait="ready") does: no hot loop.
                    if last_poll_started is not None:
                        pause = _POLL_MIN_INTERVAL_S - (_clock.monotonic() - last_poll_started)
                        if deadline is not None:
                            pause = min(pause, max(0.0, deadline - _clock.monotonic()))
                        if pause > 0:
                            await anyio.sleep(pause)
                    last_poll_started = _clock.monotonic()
                    remaining = self._remaining(deadline)
                    millis = timeout_millis_from_seconds(remaining)
                    params: JsonObject = {"wait": "ready"}
                    poll_timeout = None
                    if remaining is not None:
                        # A sub-millisecond remainder still bounds the server's long poll.
                        poll_millis = max(1, min(millis or 0, 60_000))
                        params["timeout_ms"] = poll_millis
                        poll_timeout = poll_millis / 1000 + 5
                    image = expect_image(
                        await self._send(
                            RequestSpec(
                                "GET",
                                path,
                                params=params,
                                timeout=poll_timeout,
                                unbounded_read=deadline is None,
                            ),
                            deadline,
                        )
                    )
            except (ClientTimeoutError, RequestTimeoutError) as error:
                if deadline is not None and _clock.monotonic() >= deadline:
                    raise create_error(
                        "image pull did not finish before timeout", 408, "REQUEST_TIMEOUT", "SDK"
                    ) from error
                raise
        if scope.cancelled_caught:
            raise create_error("image pull did not finish before timeout", 408, "REQUEST_TIMEOUT", "SDK")
        self._remaining(deadline)
        if wait and image.status in ("failed", "error"):
            raise ImageBuildFailedError(
                image.error or f"image pull {image.id} failed",
                status_code=409,
                code="IMAGE_BUILD_FAILED",
                source="SDK",
                image_id=image.id,
                build_error=image.error,
            )
        return image

    def _remaining(self, deadline: float | None) -> float | None:
        remaining = None if deadline is None else deadline - _clock.monotonic()
        if remaining is not None and remaining <= 0:
            raise create_error("image pull did not finish before timeout", 408, "REQUEST_TIMEOUT", "SDK")
        return remaining

    async def _send(self, spec: RequestSpec, deadline: float | None) -> TransportPayload:
        remaining = self._remaining(deadline)
        # Every request stays within the smaller of its own bound and the
        # remaining pull budget. An ordinary request (the submission, the
        # first read) keeps the client's total request timeout while that is
        # the smaller one; the enclosing cancel scope also cuts at the deadline.
        request_timeout = spec.timeout
        if remaining is not None:
            own = spec.timeout if spec.timeout is not None else self.transport.request_timeout_seconds
            if spec.timeout is not None or own <= 0 or remaining < own:
                request_timeout = min(remaining, own) if own > 0 else remaining
        payload = await self.transport.send(replace(spec, timeout=request_timeout))
        self._remaining(deadline)
        return payload
