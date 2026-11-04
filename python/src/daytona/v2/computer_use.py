# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ._core import RequestSpec, TransportPayload, expect_object
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
    ComputerUseRestartResponse,
    ComputerUseStatusResponse,
)


class AsyncComputerUseService:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport
        self.service = AsyncAddonProcessesService(transport)
        self.mouse = AsyncMouseService(transport)
        self.keyboard = AsyncKeyboardService(transport)
        self.screen = AsyncScreenService(transport)
        self.display = AsyncDisplayService(transport)
        self.a11y = AsyncA11yService(transport)
        self.recording = AsyncRecordingService(transport)

    async def status(self) -> ComputerUseStatusResponse:
        return parse_computer_use_status(
            expect_object(await self._send(RequestSpec("GET", "/computeruse/status")))
        )

    async def start(self) -> ComputerUseLifecycleResult:
        return parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(
                await self._send(RequestSpec("POST", "/computeruse/start"))
            )
        )

    async def stop(self) -> ComputerUseLifecycleResult:
        return parse_computer_use_lifecycle(
            expect_computer_use_lifecycle_response(await self._send(RequestSpec("POST", "/computeruse/stop")))
        )

    async def restart(self) -> ComputerUseRestartResponse:
        return parse_computer_use_restart(
            expect_object(await self._send(RequestSpec("POST", "/computeruse/restart")))
        )

    async def _send(self, spec: RequestSpec) -> TransportPayload:
        return await self._transport.send(spec)
