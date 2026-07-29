# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from daytona_toolbox_api_client_async import Process as ToolboxProcessRecord
from daytona_toolbox_api_client_async import ProcessLogFrame, ProcessLogPage, ProcessResult

from .._utils.errors import intercept_errors
from ..common.process import ProcessHandleJSON
from ..handle.async_pty_handle import AsyncPtyHandle
from ..internal.process_v2 import encode_process_handle_json

if TYPE_CHECKING:
    from .._async.process import AsyncProcess
    from ..common.process import ProcessLogEncoding


@dataclass(frozen=True, slots=True)
class ProcessStreamLogEvent:
    cursor: str
    frame: ProcessLogFrame
    type: Literal["log"] = "log"


@dataclass(frozen=True, slots=True)
class ProcessStreamStateEvent:
    cursor: str
    process: ToolboxProcessRecord
    type: Literal["state"] = "state"


@dataclass(frozen=True, slots=True)
class ProcessStreamWarningEvent:
    cursor: str
    message: str
    first_available_cursor: str
    type: Literal["warning"] = "warning"


@dataclass(frozen=True, slots=True)
class ProcessStreamEofEvent:
    cursor: str
    type: Literal["eof"] = "eof"


ProcessStreamEvent = ProcessStreamLogEvent | ProcessStreamStateEvent | ProcessStreamWarningEvent | ProcessStreamEofEvent


class AsyncProcessHandle:
    def __init__(self, process_client: AsyncProcess, process_id: str):
        self._process_client = process_client
        self._process_id = process_id

    @property
    def id(self) -> str:
        return self._process_id

    def to_json(self) -> ProcessHandleJSON:
        return encode_process_handle_json(sandbox_id=self._process_client.sandbox_id, process_id=self._process_id)

    @intercept_errors(message_prefix="Failed to get process: ")
    async def get(self) -> ToolboxProcessRecord:
        return await self._process_client._get_process_record(self._process_id)

    @intercept_errors(message_prefix="Failed to get process logs: ")
    async def logs(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        encoding: ProcessLogEncoding = "text",
    ) -> ProcessLogPage:
        return await self._process_client._get_process_logs(self._process_id, cursor=cursor, limit=limit, encoding=encoding)

    @intercept_errors(message_prefix="Failed to stream process logs: ")
    async def stream_logs(self, cursor: str | None = None) -> AsyncIterator[ProcessStreamEvent]:
        async for event in self._process_client._stream_process_logs(self._process_id, cursor=cursor):
            yield event

    @intercept_errors(message_prefix="Failed to send process stdin: ")
    async def stdin(self, data: str | bytes) -> None:
        await self._process_client._send_process_stdin(self._process_id, data)

    @intercept_errors(message_prefix="Failed to send process stdin EOF: ")
    async def stdin_eof(self) -> None:
        await self._process_client._send_process_stdin_eof(self._process_id)

    @intercept_errors(message_prefix="Failed to signal process: ")
    async def kill(
        self,
        signal: str = "SIGTERM",
        escalate_after_ms: int | None = None,
        escalate_to: str = "SIGKILL",
    ) -> None:
        await self._process_client._signal_process(
            self._process_id,
            signal=signal,
            escalate_after_ms=escalate_after_ms,
            escalate_to=escalate_to,
        )

    @intercept_errors(message_prefix="Failed to resize process: ")
    async def resize(self, cols: int, rows: int) -> None:
        await self._process_client._resize_process(self._process_id, cols=cols, rows=rows)

    @intercept_errors(message_prefix="Failed to wait for process: ")
    async def wait(self, timeout_ms: int | None = None) -> ProcessResult:
        return await self._process_client._wait_for_process(self._process_id, timeout_ms=timeout_ms)

    @intercept_errors(message_prefix="Failed to attach terminal: ")
    async def attach_terminal(self) -> AsyncPtyHandle:
        return await self._process_client._attach_process_terminal(self._process_id)
