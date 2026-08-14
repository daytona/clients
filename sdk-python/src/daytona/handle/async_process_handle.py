# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

from daytona_toolbox_api_client_async import Process as ToolboxProcessRecord
from daytona_toolbox_api_client_async import ProcessLogFrame, ProcessLogPage, ProcessResult

from .._utils.errors import intercept_errors
from ..common.process import ProcessLogEncoding, ProcessRunOutput
from ..handle.async_pty_handle import AsyncPtyHandle


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


class _AsyncProcessClient(Protocol):
    """Structural interface the handle needs from the process client; keeps the
    handle module free of an import cycle with the concrete client."""

    async def _get_process_record(self, process_id: str) -> ToolboxProcessRecord:
        ...

    async def _collect_run_output(self, handle: AsyncProcessHandle) -> tuple[str, str]:
        ...

    async def _cleanup_process(self, process_id: str) -> None:
        ...

    async def _get_process_logs(
        self, process_id: str, *, cursor: str | None, limit: int | None, encoding: ProcessLogEncoding
    ) -> ProcessLogPage:
        ...

    def _stream_process_logs(
        self, process_id: str, *, cursor: str | None, encoding: ProcessLogEncoding = ...
    ) -> AsyncIterator[ProcessStreamEvent]:
        ...

    async def _send_process_stdin(self, process_id: str, data: str | bytes) -> None:
        ...

    async def _send_process_stdin_eof(self, process_id: str) -> None:
        ...

    async def _signal_process(
        self, process_id: str, *, signal: str, escalate_after_ms: int | None, escalate_to: str
    ) -> None:
        ...

    async def _resize_process(self, process_id: str, *, cols: int, rows: int) -> None:
        ...

    async def _wait_for_process(self, process_id: str, *, timeout_ms: int | None = None) -> ProcessResult:
        ...

    async def _attach_process_terminal(self, process_id: str) -> AsyncPtyHandle:
        ...


class AsyncProcessHandle:
    def __init__(self, process_client: _AsyncProcessClient, process_id: str):
        self._process_client: _AsyncProcessClient = process_client
        self._process_id: str = process_id

    @property
    def id(self) -> str:
        """Process id this handle points at."""
        return self._process_id

    @intercept_errors(message_prefix="Failed to get process: ")
    async def get(self) -> ToolboxProcessRecord:
        """Fetch the current process record (state, pid, exit metadata, retention info)."""
        return await self._process_client._get_process_record(self._process_id)

    @intercept_errors(message_prefix="Failed to get process logs: ")
    async def logs(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        encoding: ProcessLogEncoding = "text",
    ) -> ProcessLogPage:
        """Read a page of retained log frames. Omit ``cursor`` (or pass ``"start"``) to
        replay from the beginning; pass the previous page's ``next_cursor`` to continue.
        Evicted history is reported via ``truncated_head``; a stale cursor raises a
        CURSOR_EXPIRED error carrying the first available cursor.
        """
        return await self._process_client._get_process_logs(
            self._process_id, cursor=cursor, limit=limit, encoding=encoding
        )

    @intercept_errors(message_prefix="Failed to stream process logs: ")
    async def stream_logs(
        self, cursor: str | None = None, encoding: ProcessLogEncoding = "text"
    ) -> AsyncIterator[ProcessStreamEvent]:
        """Stream log events live (frames, state changes, eof), optionally resuming from
        a cursor - missed output is replayed first, then live frames follow. Iteration
        ends when the process exits.
        """
        async for event in self._process_client._stream_process_logs(
            self._process_id, cursor=cursor, encoding=encoding
        ):
            yield event

    @intercept_errors(message_prefix="Failed to cleanup process: ")
    async def cleanup(self) -> None:
        """Delete the finished process's record and retained logs, freeing
        their disk footprint in the sandbox. Fails on a running process."""
        await self._process_client._cleanup_process(self._process_id)

    @intercept_errors(message_prefix="Failed to collect process output: ")
    async def output(self) -> ProcessRunOutput:
        """Collect the process's retained stdout/stderr plus exit metadata.

        Works on running processes (returns output so far) and after
        reconnecting to a finished one.
        """
        record = await self.get()
        stdout, stderr = await self._process_client._collect_run_output(self)
        return ProcessRunOutput(
            id=self._process_id,
            exit_code=record.exit_code,
            signal=record.signal,
            reason=str(getattr(record.reason or record.state, "value", record.reason or record.state)),
            stdout=stdout,
            stderr=stderr,
        )

    @intercept_errors(message_prefix="Failed to send process stdin: ")
    async def stdin(self, data: str | bytes) -> None:
        """Write data to the process's stdin (requires ``stdin="pipe"`` at start, or a PTY)."""
        await self._process_client._send_process_stdin(self._process_id, data)

    @intercept_errors(message_prefix="Failed to send process stdin EOF: ")
    async def stdin_eof(self) -> None:
        """Close the process's stdin, signalling end-of-input to programs that read until EOF."""
        await self._process_client._send_process_stdin_eof(self._process_id)

    @intercept_errors(message_prefix="Failed to signal process: ")
    async def kill(
        self,
        signal: str = "SIGTERM",
        escalate_after_ms: int | None = None,
        escalate_to: str = "SIGKILL",
    ) -> None:
        """Send a signal to the process (default SIGKILL; pass ``signal`` to override)."""
        await self._process_client._signal_process(
            self._process_id,
            signal=signal,
            escalate_after_ms=escalate_after_ms,
            escalate_to=escalate_to,
        )

    @intercept_errors(message_prefix="Failed to resize process: ")
    async def resize(self, cols: int, rows: int) -> None:
        """Resize the pseudo-terminal of a ``kind="pty"`` process."""
        await self._process_client._resize_process(self._process_id, cols=cols, rows=rows)

    @intercept_errors(message_prefix="Failed to wait for process: ")
    async def wait(self, timeout_ms: int | None = None) -> ProcessResult:
        """Block until the process exits (or ``timeout_ms`` elapses, reason ``timed_out``)
        and return the exit result. Does not collect output - pair with ``output()`` or
        use ``process.run`` when you want both.
        """
        return await self._process_client._wait_for_process(self._process_id, timeout_ms=timeout_ms)

    @intercept_errors(message_prefix="Failed to attach terminal: ")
    async def attach_terminal(self) -> AsyncPtyHandle:
        """Open the interactive bidirectional terminal socket of a ``kind="pty"`` process
        (raw terminal bytes in both directions).
        """
        return await self._process_client._attach_process_terminal(self._process_id)
