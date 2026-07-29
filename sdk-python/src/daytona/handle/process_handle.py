# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from daytona_toolbox_api_client import Process as ToolboxProcessRecord
from daytona_toolbox_api_client import ProcessLogFrame, ProcessLogPage, ProcessResult

from .._utils.errors import intercept_errors
from ..common.process import ProcessHandleJSON, ProcessLogEncoding
from ..handle.pty_handle import PtyHandle
from ..internal.process_v2 import encode_process_handle_json

if TYPE_CHECKING:
    from .._sync.process import Process as SyncProcessClient


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


class ProcessHandle:
    def __init__(self, process_client: SyncProcessClient, process_id: str):
        self._process_client = process_client
        self._process_id = process_id

    @property
    def id(self) -> str:
        return self._process_id

    def to_json(self) -> ProcessHandleJSON:
        return encode_process_handle_json(sandbox_id=self._process_client.sandbox_id, process_id=self._process_id)

    @intercept_errors(message_prefix="Failed to get process: ")
    def get(self) -> ToolboxProcessRecord:
        return self._process_client._get_process_record(self._process_id)

    @intercept_errors(message_prefix="Failed to get process logs: ")
    def logs(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        encoding: ProcessLogEncoding = "text",
    ) -> ProcessLogPage:
        return self._process_client._get_process_logs(self._process_id, cursor=cursor, limit=limit, encoding=encoding)

    @intercept_errors(message_prefix="Failed to stream process logs: ")
    def stream_logs(self, cursor: str | None = None) -> Iterator[ProcessStreamEvent]:
        yield from self._process_client._stream_process_logs(self._process_id, cursor=cursor)

    @intercept_errors(message_prefix="Failed to send process stdin: ")
    def stdin(self, data: str | bytes) -> None:
        self._process_client._send_process_stdin(self._process_id, data)

    @intercept_errors(message_prefix="Failed to send process stdin EOF: ")
    def stdin_eof(self) -> None:
        self._process_client._send_process_stdin_eof(self._process_id)

    @intercept_errors(message_prefix="Failed to signal process: ")
    def kill(
        self,
        signal: str = "SIGTERM",
        escalate_after_ms: int | None = None,
        escalate_to: str = "SIGKILL",
    ) -> None:
        self._process_client._signal_process(
            self._process_id,
            signal=signal,
            escalate_after_ms=escalate_after_ms,
            escalate_to=escalate_to,
        )

    @intercept_errors(message_prefix="Failed to resize process: ")
    def resize(self, cols: int, rows: int) -> None:
        self._process_client._resize_process(self._process_id, cols=cols, rows=rows)

    @intercept_errors(message_prefix="Failed to wait for process: ")
    def wait(self, timeout_ms: int | None = None) -> ProcessResult:
        return self._process_client._wait_for_process(self._process_id, timeout_ms=timeout_ms)

    @intercept_errors(message_prefix="Failed to attach terminal: ")
    def attach_terminal(self) -> PtyHandle:
        return self._process_client._attach_process_terminal(self._process_id)
