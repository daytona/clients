# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ._core import RequestSpec, TransportPayload
from ._transport import AsyncTransport
from .computer_use_core import (
    expect_computer_use_lifecycle_response,
    parse_computer_use_lifecycle,
    parse_computer_use_restart,
    parse_computer_use_status,
)
from .computer_use_services import (
    AsyncA11yService,
    AsyncAddonProcessesService,
    AsyncDisplayService,
    AsyncKeyboardService,
    AsyncMouseService,
    AsyncRecordingService,
    AsyncScreenService,
)
from .computer_use_types import (
    ComputerUseLifecycleResult,
    ComputerUseResolution,
    ComputerUseRestartResponse,
    ComputerUseStatusResponse,
)
from .types import JsonValue


def _start_body(resolution: ComputerUseResolution | None) -> JsonValue:
    if resolution is None:
        return None
    return {"resolution": {"width": resolution.width, "height": resolution.height}}


class AsyncComputerUseService:
    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport
        self.service = AsyncAddonProcessesService(_transport)
        self.mouse = AsyncMouseService(_transport)
        self.keyboard = AsyncKeyboardService(_transport)
        self.screen = AsyncScreenService(_transport)
        self.display = AsyncDisplayService(_transport)
        self.a11y = AsyncA11yService(_transport)
        self.recording = AsyncRecordingService(_transport)

    async def status(self) -> ComputerUseStatusResponse:
        return parse_computer_use_status(await self._send(RequestSpec("GET", "/computeruse/status")))

    async def start(self, *, resolution: ComputerUseResolution | None = None) -> ComputerUseLifecycleResult:
        """Start the desktop.

        ``resolution`` selects its size (1024x768, 1280x720 or 1920x1080) and is
        kept for later starts and restarts. Omitted, the desktop uses the
        resolution it last started at, or 1280x720. Starting a running desktop at
        a different resolution raises ``ConflictError``; call ``restart`` with
        the new resolution instead. Linux sandboxes only: other platforms raise
        ``UnsupportedOperationError`` for a ``resolution``.
        """
        return parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(
                await self._send(RequestSpec("POST", "/computeruse/start", json_body=_start_body(resolution)))
            )
        )

    async def stop(self) -> ComputerUseLifecycleResult:
        return parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(await self._send(RequestSpec("POST", "/computeruse/stop")))
        )

    async def restart(self, *, resolution: ComputerUseResolution | None = None) -> ComputerUseRestartResponse:
        """Restart the desktop, at ``resolution`` when given (kept for later
        starts). Omitted, it restarts at its current resolution. Linux sandboxes
        only: other platforms raise ``UnsupportedOperationError`` for a
        ``resolution``.
        """
        return parse_computer_use_restart(
            await self._send(RequestSpec("POST", "/computeruse/restart", json_body=_start_body(resolution)))
        )

    async def _send(self, spec: RequestSpec) -> TransportPayload:
        return await self._transport.send(spec)
