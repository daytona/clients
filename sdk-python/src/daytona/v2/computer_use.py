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
    ComputerUseRestartResponse,
    ComputerUseStatusResponse,
)


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
        return parse_computer_use_restart(await self._send(RequestSpec("POST", "/computeruse/restart")))

    async def _send(self, spec: RequestSpec) -> TransportPayload:
        return await self._transport.send(spec)
