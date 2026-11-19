# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

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


def _start_body(
    resolution: ComputerUseResolution | None, environment: Literal["xfce", "gnome"] | None
) -> JsonValue:
    body: dict[str, JsonValue] = {}
    if resolution is not None:
        body["resolution"] = {"width": resolution.width, "height": resolution.height}
    if environment is not None:
        body["environment"] = environment
    return body or None


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
        """Read the desktop state, resolution and environment. On Linux,
        environment is the running desktop's, otherwise the next start's:
        the last selected, or xfce. Settings are absent on Windows and macOS
        and may be absent on older guests. Unknown environments retain their
        raw_environment.
        """
        return parse_computer_use_status(await self._send(RequestSpec("GET", "/computeruse/status")))

    async def start(
        self,
        *,
        resolution: ComputerUseResolution | None = None,
        environment: Literal["xfce", "gnome"] | None = None,
    ) -> ComputerUseLifecycleResult:
        """Start the desktop.

        ``resolution`` selects its size (1024x768, 1280x720 or 1920x1080) and is
        kept for later starts and restarts. Omitted, the desktop uses the
        resolution it last started at, or 1280x720. ``environment`` selects
        xfce or gnome, kept for later starts; omitted, the last selected, or
        xfce. GNOME needs a system layer with GNOME, a successful preflight,
        at least 4 vCPUs and 8192 MiB. Unsupported settings or insufficient
        resources raise ``BadRequestError``. Starting a running desktop with
        different settings raises ``ConflictError``; use ``restart`` instead.
        Windows and macOS refuse either setting with ``UnsupportedOperationError``,
        as does a system layer without GNOME when it is requested. With either
        setting, the SDK reads status once and raises ``UnsupportedOperationError``
        if the environment is absent or differs, or the resolution differs
        (falling back to display info when status has no resolution). A guest
        with resolution selection but without environment selection refuses
        ``environment`` with 422 ``UnprocessableEntityError``; older guests
        ignore the body and start XFCE. If a read fails, its error is raised
        although the start took effect; starting again with the same settings
        is a no-op.
        """
        started = parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(
                await self._send(
                    RequestSpec("POST", "/computeruse/start", json_body=_start_body(resolution, environment))
                )
            )
        )
        if resolution is not None or environment is not None:
            await self._read_back_settings(resolution, environment)
        return started

    async def stop(self) -> ComputerUseLifecycleResult:
        return parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(await self._send(RequestSpec("POST", "/computeruse/stop")))
        )

    async def restart(
        self,
        *,
        resolution: ComputerUseResolution | None = None,
        environment: Literal["xfce", "gnome"] | None = None,
    ) -> ComputerUseRestartResponse:
        """Restart with optional ``resolution`` and ``environment`` (xfce or
        gnome), kept for later starts. Omitted fields reuse the last selection,
        defaulting to 1280x720 and xfce. Use this instead of ``start`` to change
        a running desktop's settings. GNOME needs a system layer with GNOME,
        a successful preflight, at least 4 vCPUs and 8192 MiB; insufficient
        resources or unsupported settings raise ``BadRequestError``.
        Windows and macOS refuse either setting with ``UnsupportedOperationError``,
        as does a system layer without GNOME when it is requested. A concurrent
        restart with different settings raises ``ConflictError``. With either
        setting, the SDK confirms it as ``start`` does, sharing one status read.
        """
        restarted = parse_computer_use_restart(
            await self._send(
                RequestSpec("POST", "/computeruse/restart", json_body=_start_body(resolution, environment))
            )
        )
        if resolution is not None or environment is not None:
            await self._read_back_settings(resolution, environment)
        return restarted

    async def _read_back_settings(
        self, requested: ComputerUseResolution | None, environment: Literal["xfce", "gnome"] | None
    ) -> None:
        """Confirm requested settings with one status read. When status has no
        resolution, use the first display. Older guests can ignore the request,
        so an absent or different environment or a different resolution raises
        instead of reporting success. A failed read raises too."""
        status = await self.status()
        if environment is not None and status.environment != environment:
            runs_environment = status.raw_environment or "an unknown environment"
            raise UnsupportedOperationError(
                f"desktop runs {runs_environment}, not the requested {environment}; "
                "the guest did not confirm environment selection",
                status_code=400,
                code=codes.UNSUPPORTED_OPERATION,
                source="SDK",
            )
        if requested is None:
            return
        actual = status.resolution
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
