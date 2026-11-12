# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from . import _codes as codes
from ._core import RequestSpec, TransportPayload
from ._transport import AsyncTransport
from .computer_use_core import (
    expect_computer_use_display_info_response,
    expect_computer_use_lifecycle_response,
    parse_computer_use_displays,
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
from .errors import UnsupportedOperationError
from .types import JsonValue


def _start_body(resolution: ComputerUseResolution | None) -> JsonValue:
    if resolution is None:
        return None
    return {"resolution": {"width": resolution.width, "height": resolution.height}}


def _format_resolution(resolution: ComputerUseResolution) -> str:
    return f"{resolution.width}x{resolution.height}"


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
        ``UnsupportedOperationError`` for a ``resolution``. With a
        ``resolution``, the SDK reads back the size the desktop runs at and
        raises ``UnsupportedOperationError`` when it differs: a guest that
        predates resolution selection ignores it, and the desktop keeps running
        at its own. If that read itself fails, its error is raised although the
        start took effect; starting again at the same resolution is a no-op.
        """
        started = parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(
                await self._send(RequestSpec("POST", "/computeruse/start", json_body=_start_body(resolution)))
            )
        )
        if resolution is not None:
            await self._read_back_resolution(resolution)
        return started

    async def stop(self) -> ComputerUseLifecycleResult:
        return parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(await self._send(RequestSpec("POST", "/computeruse/stop")))
        )

    async def restart(self, *, resolution: ComputerUseResolution | None = None) -> ComputerUseRestartResponse:
        """Restart the desktop, at ``resolution`` when given (kept for later
        starts). Omitted, it restarts at its current resolution. Linux sandboxes
        only: other platforms raise ``UnsupportedOperationError`` for a
        ``resolution``. With a ``resolution``, the SDK reads it back as
        ``start`` does.
        """
        restarted = parse_computer_use_restart(
            await self._send(RequestSpec("POST", "/computeruse/restart", json_body=_start_body(resolution)))
        )
        if resolution is not None:
            await self._read_back_resolution(resolution)
        return restarted

    async def _read_back_resolution(self, requested: ComputerUseResolution) -> None:
        """After a start or restart that asked for ``requested``, read back the
        size the desktop runs at: the status ``resolution``, or, from a guest
        that predates it, the first display. A guest that predates resolution
        selection answers 2xx and ignores the request, so a mismatch raises
        instead of reporting success. A read that fails raises too: success is
        never reported for a resolution that was not confirmed."""
        actual = (await self.status()).resolution
        if actual is None:
            displays = parse_computer_use_displays(
                expect_computer_use_display_info_response(
                    await self._send(RequestSpec("GET", "/computeruse/display/info"))
                )
            )
            if displays:
                actual = ComputerUseResolution(width=displays[0].width, height=displays[0].height)
        if actual is not None and actual.width == requested.width and actual.height == requested.height:
            return
        runs_at = "an unknown resolution" if actual is None else _format_resolution(actual)
        raise UnsupportedOperationError(
            f"desktop runs at {runs_at}, not the requested {_format_resolution(requested)}; "
            "a guest that predates resolution selection ignores it",
            status_code=400,
            code=codes.UNSUPPORTED_OPERATION,
            source="SDK",
        )

    async def _send(self, spec: RequestSpec) -> TransportPayload:
        return await self._transport.send(spec)
