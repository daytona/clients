# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from typing import TypeVar, cast
from urllib.parse import urlencode

import httpx
import httpx_ws
from pydantic import ValidationError

from daytona_toolbox_api_client import (
    CodeRunRequest,
    Command,
    CreateProcessRequest,
    CreateSessionRequest,
    ExecuteRequest,
    KillProcessRequest,
)
from daytona_toolbox_api_client import Process as ToolboxProcessRecord
from daytona_toolbox_api_client import (
    ProcessApi,
    ProcessLogFrame,
    ProcessLogPage,
    ProcessResult,
    ProcessStdinRequest,
    PtyResizeRequest,
    PtySessionInfo,
    ResizeProcessRequest,
    Session,
    SessionSendInputRequest,
)
from daytona_toolbox_api_client.exceptions import ApiException as ToolboxApiException

from .._utils.errors import create_daytona_error, intercept_errors
from .._utils.otel_decorator import with_instrumentation
from .._utils.stream import std_demux_stream_httpx_ws
from .._utils.timeout import http_timeout
from ..common.charts import parse_chart
from ..common.errors import SOURCE_DAEMON, DaytonaError, DaytonaUnsupportedOperationError, DaytonaValidationError
from ..common.process import (
    CodeRunParams,
    ExecuteResponse,
    ExecutionArtifacts,
    OutputHandler,
    ProcessKeepLogsName,
    ProcessKindName,
    ProcessLogEncoding,
    ProcessRunOutput,
    ProcessStateFilter,
    ProcessStdinModeName,
    RunFrameDecoder,
    SessionCommandLogsResponse,
    SessionExecuteRequest,
    SessionExecuteResponse,
)
from ..common.pty import PTY_EXIT_CONTROL_SUBPROTOCOL, PtyResult, PtySize
from ..handle.process_handle import (
    ProcessHandle,
    ProcessStreamEofEvent,
    ProcessStreamEvent,
    ProcessStreamLogEvent,
    ProcessStreamStateEvent,
    ProcessStreamWarningEvent,
)
from ..handle.pty_handle import PtyHandle
from ..internal.process import (
    CreateProcessPayload,
    KillProcessPayload,
    ProcessStdinPayload,
    ProcessTerminalPayload,
    ResizeProcessPayload,
    normalize_process_stdin,
    parse_json_object,
    process_error_from_response,
    pty_result_from_process_result,
)
from ..internal.sse import ServerSentEvent, ServerSentEventParser

_ProcessResultT = TypeVar("_ProcessResultT")


class Process:
    """Handles process and code execution within a Sandbox."""

    def __init__(
        self,
        language: str,
        api_client: ProcessApi,
        http_client: httpx.Client,
    ):
        """Initialize a new Process instance.

        Args:
            api_client (ProcessApi): API client for process operations.
            http_client: Shared httpx.Client whose connection pool the WS upgrade reuses.
        """
        self._language: str = language
        self._api_client: ProcessApi = api_client
        self._http_client: httpx.Client = http_client

    async def _consume_log_websocket(
        self,
        url: str,
        headers: dict[str, str],
        on_stdout: OutputHandler[str],
        on_stderr: OutputHandler[str],
    ) -> None:
        """Open a sync httpx_ws WebSocket, demux stdout/stderr until EOF, then close.

        The surrounding method is ``async def`` (callable via ``asyncio.run`` from sync code)
        so user-supplied callbacks may be ``async def`` and need awaiting. The httpx_ws session
        itself is sync — its blocking ``receive()`` is bridged onto a worker thread inside
        :func:`std_demux_stream_httpx_ws` so we don't stall the event loop. The WS upgrade
        reuses ``self._http_client``'s connection pool, sharing TLS context + DNS cache with
        every other sync HTTP request the SDK makes.
        """
        with httpx_ws.connect_ws(url, self._http_client, headers=headers) as ws:
            await std_demux_stream_httpx_ws(ws, on_stdout, on_stderr)

    @intercept_errors(message_prefix="Failed to execute command: ")
    @with_instrumentation()
    def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command in the Sandbox.

        Args:
            command (str): Shell command to execute.
            cwd (str | None): Working directory for command execution. If not
                specified, uses the sandbox working directory.
            env (dict[str, str] | None): Environment variables to set for the command.
            timeout (int | None): Maximum time in seconds to wait for the command
                to complete.

        Returns:
            ExecuteResponse: Command execution results containing:
                - exit_code: The command's exit status
                - result: Standard output from the command
                - artifacts: ExecutionArtifacts object containing `stdout` (same as result)
                and `charts` (matplotlib charts metadata)

        Example:
            ```python
            # Simple command
            response = sandbox.process.exec("echo 'Hello'")
            print(response.artifacts.stdout)  # Prints: Hello

            # Command with working directory
            result = sandbox.process.exec("ls", cwd="workspace/src")

            # Command with timeout
            result = sandbox.process.exec("sleep 10", timeout=5)
            ```
        """
        execute_request = ExecuteRequest(command=command, cwd=cwd, timeout=timeout, envs=env)

        response = self._api_client.execute_command(
            request=execute_request,
            _request_timeout=http_timeout(timeout + 5 if timeout else None),
        )

        result = response.result or ""
        artifacts = ExecutionArtifacts(stdout=result, charts=[])

        return ExecuteResponse.model_construct(
            exit_code=(
                response.exit_code if response.exit_code is not None else response.additional_properties.get("code")
            ),
            result=result,
            artifacts=artifacts,
            additional_properties=response.additional_properties,
        )

    @with_instrumentation()
    def code_run(
        self,
        code: str,
        params: CodeRunParams | None = None,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Executes code in the Sandbox using the appropriate language runtime.

        Args:
            code (str): Code to execute.
            params (CodeRunParams | None): Parameters for code execution.
            timeout (int | None): Maximum time in seconds to wait for the code
                to complete.

        Returns:
            ExecuteResponse: Code execution result containing:
                - exit_code: The execution's exit status
                - result: Standard output from the code
                - artifacts: ExecutionArtifacts object containing `stdout` (same as result)
                and `charts` (matplotlib charts metadata)

        Example:
            ```python
            # Run Python code
            response = sandbox.process.code_run('''
                x = 10
                y = 20
                print(f"Sum: {x + y}")
            ''')
            print(response.artifacts.stdout)  # Prints: Sum: 30
            ```

            Matplotlib charts are automatically detected and returned in the `charts` field
            of the `ExecutionArtifacts` object.
            ```python
            code = '''
            import matplotlib.pyplot as plt
            import numpy as np

            x = np.linspace(0, 10, 30)
            y = np.sin(x)

            plt.figure(figsize=(8, 5))
            plt.plot(x, y, 'b-', linewidth=2)
            plt.title('Line Chart')
            plt.xlabel('X-axis (seconds)')
            plt.ylabel('Y-axis (amplitude)')
            plt.grid(True)
            plt.show()
            '''

            response = sandbox.process.code_run(code)
            chart = response.artifacts.charts[0]

            print(f"Type: {chart.type}")
            print(f"Title: {chart.title}")
            if chart.type == ChartType.LINE and isinstance(chart, LineChart):
                print(f"X Label: {chart.x_label}")
                print(f"Y Label: {chart.y_label}")
                print(f"X Ticks: {chart.x_ticks}")
                print(f"X Tick Labels: {chart.x_tick_labels}")
                print(f"X Scale: {chart.x_scale}")
                print(f"Y Ticks: {chart.y_ticks}")
                print(f"Y Tick Labels: {chart.y_tick_labels}")
                print(f"Y Scale: {chart.y_scale}")
                print("Elements:")
                for element in chart.elements:
                    print(f"Label: {element.label}")
                    print(f"Points: {element.points}")
            ```
        """
        code_run_params = params or CodeRunParams()
        code_run_request = CodeRunRequest(
            code=code,
            language=self._language,
            argv=code_run_params.argv,
            envs=code_run_params.env,
            timeout=timeout,
        )

        response = self._api_client.code_run(
            request=code_run_request,
            _request_timeout=http_timeout(timeout + 5 if timeout else None),
        )

        stdout = response.result or ""
        charts = []
        if response.artifacts and response.artifacts.charts:
            charts = [parse_chart(chart) for chart in response.artifacts.charts]
        artifacts = ExecutionArtifacts(stdout=stdout, charts=charts)

        # TODO: Remove model_construct once everything is migrated to pydantic # pylint: disable=fixme
        return ExecuteResponse.model_construct(
            exit_code=(
                response.exit_code if response.exit_code is not None else response.additional_properties.get("code")
            ),
            result=stdout,
            artifacts=artifacts,
            additional_properties=response.additional_properties,
        )

    @intercept_errors(message_prefix="Failed to start process: ")
    @with_instrumentation()
    def start(
        self,
        *,
        argv: list[str] | None = None,
        shell_command: str | None = None,
        shell: str | None = None,
        login: bool = False,
        name: str | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        user: str | None = None,
        stdin: ProcessStdinModeName | None = None,
        timeout_ms: int | None = None,
        kind: ProcessKindName | None = None,
        terminal: PtySize | None = None,
        term: str | None = None,
        keep_logs: ProcessKeepLogsName | None = None,
    ) -> ProcessHandle:
        """Start a process in the background and return immediately with a handle.

        Use ``start`` for long-running or interactive work you want to supervise
        yourself: stream logs, write stdin, resize a PTY, kill, or reconnect later
        (reconnect from any client later via ``connect`` with the process id). The
        process and its logs are retained after exit until ``handle.cleanup()``.
        For one-shot commands where you just want the output, prefer ``run``.

        Example:
            ```python
            handle = sandbox.process.start(shell_command="npm run dev", name="dev-server")
            for event in handle.stream_logs():
                ...
            ```
        """
        process = self._create_process(
            argv=argv,
            shell_command=shell_command,
            shell=shell,
            login=login,
            name=name,
            session_id=session_id,
            cwd=cwd,
            env=env,
            user=user,
            stdin=stdin,
            timeout_ms=timeout_ms,
            kind=kind,
            terminal=terminal,
            term=term,
            keep_logs=keep_logs,
        )
        return ProcessHandle(self, process.id)

    @intercept_errors(message_prefix="Failed to run process: ")
    @with_instrumentation()
    def run(
        self,
        *,
        argv: list[str] | None = None,
        shell_command: str | None = None,
        shell: str | None = None,
        login: bool = False,
        name: str | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        user: str | None = None,
        stdin: ProcessStdinModeName | None = None,
        timeout_ms: int | None = None,
        wait_timeout_ms: int | None = None,
        kind: ProcessKindName | None = None,
        terminal: PtySize | None = None,
        term: str | None = None,
        keep_logs: ProcessKeepLogsName | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> ProcessRunOutput:
        """Run a process to completion and return its collected output - the
        "just give me the result" counterpart to ``start``.

        Waits for exit (or ``wait_timeout_ms``), gathers stdout/stderr (UTF-8 safe,
        with optional ``on_stdout``/``on_stderr`` streaming callbacks) and returns
        exit metadata. Logs are retained only briefly after exit (``on_exit_ttl``),
        so read what you need from the result.

        The returned ``stdout``/``stderr`` are limited to what daemon log retention still
        holds: a process that outruns the retention budget keeps only the retained suffix.
        When every byte matters, pass ``on_stdout``/``on_stderr`` here - they receive output
        as it streams - or use ``start`` instead and call ``stream_logs`` on the returned
        handle before retention evicts anything.

        Example:
            ```python
            result = sandbox.process.run(shell_command="ls -la")
            print(result.exit_code, result.stdout)
            ```
        """
        process = self._create_process(
            argv=argv,
            shell_command=shell_command,
            shell=shell,
            login=login,
            name=name,
            session_id=session_id,
            cwd=cwd,
            env=env,
            user=user,
            stdin=stdin,
            timeout_ms=timeout_ms,
            kind=kind,
            terminal=terminal,
            term=term,
            keep_logs=keep_logs or "on_exit_ttl",
        )
        handle = ProcessHandle(self, process.id)

        if on_stdout is not None or on_stderr is not None:
            stdout, stderr, timed_out = self._stream_run_output(handle, on_stdout, on_stderr, wait_timeout_ms)
            result = self._wait_for_process(process.id, timeout_ms=1 if timed_out else None)
        else:
            result = self._wait_for_process(process.id, timeout_ms=wait_timeout_ms)
            stdout, stderr = self._collect_run_output(handle)

        return ProcessRunOutput(
            id=process.id,
            exit_code=result.exit_code,
            signal=result.signal,
            reason=result.reason,
            stdout=stdout,
            stderr=stderr,
        )

    def _stream_run_output(
        self,
        handle: ProcessHandle,
        on_stdout: Callable[[str], None] | None,
        on_stderr: Callable[[str], None] | None,
        wait_timeout_ms: int | None,
    ) -> tuple[str, str, bool]:
        stdout: list[str] = []
        stderr: list[str] = []
        decoder = RunFrameDecoder()
        budget_s = wait_timeout_ms / 1000 if wait_timeout_ms else None
        deadline = time.monotonic() + budget_s if budget_s is not None else None
        timed_out = False
        # Two mechanisms are needed to honour the budget: the deadline check below stops a
        # chatty stream as soon as the next event lands, while the transport read timeout
        # stops a silent one (no events, no daemon heartbeat). A single idle read is
        # therefore bounded by the full budget rather than by the exact remaining slice.
        # Narrowing that to the remaining slice is not reachable from here: httpcore samples
        # the read timeout once per response body, and closing the response from a watchdog
        # thread neither wakes the blocked recv nor leaves the socket safe to keep reading.
        try:
            for event in self._stream_process_logs(
                handle.id, cursor="start", encoding="base64", read_timeout_s=budget_s
            ):
                if event.type == "log":
                    side, data = decoder.decode(event.frame.channel, event.frame.data or "", event.frame.encoding)
                    if side == "stdout" and data:
                        stdout.append(data)
                        if on_stdout is not None:
                            on_stdout(data)
                    elif side == "stderr" and data:
                        stderr.append(data)
                        if on_stderr is not None:
                            on_stderr(data)
                elif event.type == "eof":
                    break
                if deadline is not None and time.monotonic() > deadline:
                    timed_out = True
                    break
        except httpx.TimeoutException:
            timed_out = True
        stdout_tail, stderr_tail = decoder.flush()
        if stdout_tail:
            stdout.append(stdout_tail)
            if on_stdout is not None:
                on_stdout(stdout_tail)
        if stderr_tail:
            stderr.append(stderr_tail)
            if on_stderr is not None:
                on_stderr(stderr_tail)
        return "".join(stdout), "".join(stderr), timed_out

    def _collect_run_output(self, handle: ProcessHandle) -> tuple[str, str]:
        """Page through the retained log frames of ``handle`` and split them per channel.

        The result is bounded by daemon log retention, not by everything the process ever
        wrote: once the ring buffer evicts a process's oldest frames, replaying from
        ``"start"`` returns the retained suffix and the page reports ``truncated_head``.
        Callers that must not miss output should stream live (``stream_logs`` surfaces a
        warning event carrying ``first_available_cursor``) instead of collecting after exit.
        """
        stdout: list[str] = []
        stderr: list[str] = []
        decoder = RunFrameDecoder()
        cursor: str | None = "start"
        for _ in range(10_000):
            page = handle.logs(cursor=cursor, limit=1000, encoding="base64")
            for frame in page.frames:
                side, data = decoder.decode(frame.channel, frame.data or "", frame.encoding)
                if side == "stdout" and data:
                    stdout.append(data)
                elif side == "stderr" and data:
                    stderr.append(data)
            if page.eof or not page.frames:
                break
            cursor = page.next_cursor
        stdout_tail, stderr_tail = decoder.flush()
        stdout.append(stdout_tail)
        stderr.append(stderr_tail)
        return "".join(stdout), "".join(stderr)

    @intercept_errors(message_prefix="Failed to connect to process: ")
    @with_instrumentation()
    def connect(self, id: str) -> ProcessHandle:
        """Attach to an existing process by id. Alias of :meth:`get` for
        reattaching with a stored process id."""
        return self.get(id)

    @intercept_errors(message_prefix="Failed to get process: ")
    @with_instrumentation()
    def get(self, id: str) -> ProcessHandle:
        """Return a handle for an existing process by id, running or finished.

        The handle can replay retained logs from the start, resume streaming from
        a cursor, or wait for exit.
        """
        _ = self._get_process_record(id)
        return ProcessHandle(self, id)

    @intercept_errors(message_prefix="Failed to list processes: ")
    @with_instrumentation()
    def list(
        self,
        *,
        state: ProcessStateFilter | None = None,
        kind: ProcessKindName | None = None,
        session_id: str | None = None,
        name: str | None = None,
        pid: int | None = None,
    ) -> list[ProcessHandle]:
        """List processes in the Sandbox, newest first - including processes started
        by other clients (the daemon is the source of truth). Filter by state,
        kind, name or session_id.

        Example:
            ```python
            running = sandbox.process.list(state="running")
            ```
        """
        processes = self._invoke_process(
            self._api_client.list_processes,
            state=state,
            kind=kind,
            session_id=session_id,
            name=name,
            pid=pid,
        )
        return [ProcessHandle(self, process.id) for process in processes]

    def _create_process(
        self,
        *,
        argv: list[str] | None,
        shell_command: str | None,
        shell: str | None,
        login: bool,
        name: str | None,
        session_id: str | None,
        cwd: str | None,
        env: dict[str, str] | None,
        user: str | None,
        stdin: ProcessStdinModeName | None,
        timeout_ms: int | None,
        kind: ProcessKindName | None,
        terminal: PtySize | None,
        term: str | None,
        keep_logs: ProcessKeepLogsName | None,
    ) -> ToolboxProcessRecord:
        has_argv = argv is not None and len(argv) > 0
        has_shell_command = shell_command is not None and shell_command.strip() != ""
        is_pty = kind == "pty"

        if has_argv and has_shell_command:
            raise DaytonaValidationError("Process start requires exactly one of argv or shell_command")
        if not has_argv and not has_shell_command and not is_pty:
            raise DaytonaValidationError("Process start requires exactly one of argv or shell_command")
        if term is not None and terminal is None:
            raise DaytonaValidationError("Process terminal term requires terminal size")

        payload: CreateProcessPayload = {}
        if has_argv and argv is not None:
            payload["argv"] = argv
        if has_shell_command and shell_command is not None:
            payload["shell_command"] = shell_command
        if shell is not None:
            payload["shell"] = shell
        if login:
            payload["login"] = True
        if name is not None:
            payload["name"] = name
        if session_id is not None:
            payload["session_id"] = session_id
        if cwd is not None:
            payload["cwd"] = cwd
        if env is not None:
            payload["env"] = env
        if user is not None:
            payload["user"] = user
        if stdin is not None:
            payload["stdin"] = stdin
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        if kind is not None:
            payload["kind"] = kind
        if terminal is not None:
            terminal_payload: ProcessTerminalPayload = {"cols": terminal.cols, "rows": terminal.rows}
            if term is not None:
                terminal_payload["term"] = term
            payload["terminal"] = terminal_payload
        if keep_logs is not None:
            payload["keep_logs"] = keep_logs

        request = CreateProcessRequest.model_validate(payload)
        return self._invoke_process(self._api_client.create_process, request=request)

    def _get_process_record(self, process_id: str) -> ToolboxProcessRecord:
        return self._invoke_process(self._api_client.get_process, id=process_id)

    def _cleanup_process(self, process_id: str) -> None:
        self._invoke_process(self._api_client.cleanup_process, id=process_id)

    def _get_process_logs(
        self,
        process_id: str,
        *,
        cursor: str | None,
        limit: int | None,
        encoding: ProcessLogEncoding,
    ) -> ProcessLogPage:
        return self._invoke_process(
            self._api_client.read_process_logs,
            id=process_id,
            cursor=cursor,
            limit=limit,
            encoding=encoding,
        )

    def _stream_process_logs(
        self,
        process_id: str,
        *,
        cursor: str | None,
        encoding: ProcessLogEncoding = "text",
        read_timeout_s: float | None = None,
    ) -> Iterator[ProcessStreamEvent]:
        _, url, headers, *_ = self._api_client._read_process_logs_serialize(
            id=process_id,
            cursor=cursor,
            limit=None,
            encoding=encoding,
            follow=True,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        stream_headers = dict(headers)
        stream_headers["Accept"] = "text/event-stream"

        # ``read_timeout_s`` bounds every blocking socket read: the daemon emits no
        # heartbeat on an idle stream, and a blocking read cannot be interrupted from
        # Python, so a transport read timeout is the only way a caller deadline can be
        # enforced while nothing arrives. ``None`` (the default) follows the stream for
        # as long as the process lives.
        parser = ServerSentEventParser()
        with self._http_client.stream("GET", url, headers=stream_headers, timeout=read_timeout_s) as response:
            if response.status_code >= 400:
                body = response.read().decode("utf-8", errors="replace")
                raise process_error_from_response(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=body,
                    fallback_message=body,
                )

            for line in response.iter_lines():
                raw_event = parser.feed_line(line)
                if raw_event is None:
                    continue
                event = self._deserialize_stream_event(raw_event)
                yield event
                if event.type == "eof":
                    return

            final_event = parser.finalize()
            if final_event is None:
                return
            event = self._deserialize_stream_event(final_event)
            yield event

    def _send_process_stdin(self, process_id: str, data: str | bytes) -> None:
        payload: ProcessStdinPayload = {"data": normalize_process_stdin(data)}
        request = ProcessStdinRequest.model_validate(payload)
        _ = self._invoke_process(self._api_client.send_process_stdin, id=process_id, request=request)

    def _send_process_stdin_eof(self, process_id: str) -> None:
        payload: ProcessStdinPayload = {"eof": True}
        request = ProcessStdinRequest.model_validate(payload)
        _ = self._invoke_process(self._api_client.send_process_stdin, id=process_id, request=request)

    def _signal_process(
        self,
        process_id: str,
        *,
        signal: str,
        escalate_after_ms: int | None,
        escalate_to: str,
    ) -> None:
        payload: KillProcessPayload = {"signal": signal}
        if escalate_after_ms is not None:
            payload["escalate_after_ms"] = escalate_after_ms
            payload["escalate_to"] = escalate_to
        request = KillProcessRequest.model_validate(payload)
        _ = self._invoke_process(self._api_client.signal_process, id=process_id, request=request)

    def _resize_process(self, process_id: str, *, cols: int, rows: int) -> None:
        payload: ResizeProcessPayload = {"cols": cols, "rows": rows}
        request = ResizeProcessRequest.model_validate(payload)
        _ = self._invoke_process(self._api_client.resize_process, id=process_id, request=request)

    def _wait_for_process(self, process_id: str, *, timeout_ms: int | None = None) -> ProcessResult:
        return self._invoke_process(self._api_client.wait_for_process, id=process_id, timeout_ms=timeout_ms)

    def _attach_process_terminal(self, process_id: str) -> PtyHandle:
        process = self._get_process_record(process_id)
        if process.kind != "pty":
            raise DaytonaUnsupportedOperationError(
                "attach is only supported for kind=pty processes",
                status_code=400,
                code="UNSUPPORTED_OPERATION",
                source=SOURCE_DAEMON,
            )

        _, url, headers, *_ = self._api_client._attach_process_serialize(
            id=process_id,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        ws_url = re.sub(r"^http", "ws", url)

        try:
            ws_cm = httpx_ws.connect_ws(ws_url, self._http_client, headers=headers)
            ws = ws_cm.__enter__()  # pylint: disable=unnecessary-dunder-call
        except httpx_ws.WebSocketUpgradeError as e:
            raise create_daytona_error(
                f"WebSocket upgrade failed with HTTP {e.response.status_code}",
                status_code=e.response.status_code,
                headers=e.response.headers,
            ) from e

        def resize_handler(pty_size: PtySize) -> PtySessionInfo:
            self._resize_process(process_id, cols=pty_size.cols, rows=pty_size.rows)
            return PtySessionInfo.model_validate(
                {
                    "active": True,
                    "cols": pty_size.cols,
                    "createdAt": process.created_at,
                    "cwd": process.cwd,
                    "envs": process.env or {},
                    "id": process_id,
                    "lazyStart": False,
                    "rows": pty_size.rows,
                }
            )

        def kill_handler() -> None:
            self._signal_process(process_id, signal="SIGTERM", escalate_after_ms=None, escalate_to="SIGKILL")

        def result_resolver(*, timeout_ms: int | None = None) -> PtyResult:
            result = self._wait_for_process(process_id, timeout_ms=timeout_ms)
            return pty_result_from_process_result(
                exit_code=result.exit_code,
                reason=result.reason,
                signal=result.signal,
            )

        return PtyHandle(
            ws,
            session_id=process_id,
            handle_resize=resize_handler,
            handle_kill=kill_handler,
            ws_context_manager=ws_cm,
            result_resolver=result_resolver,
            connection_established=True,
        )

    def _deserialize_stream_event(self, event: ServerSentEvent) -> ProcessStreamEvent:
        match event.event:
            case "log":
                frame = ProcessLogFrame.from_json(event.data)
                if frame is None:
                    raise DaytonaError("Process log event payload is missing")
                return ProcessStreamLogEvent(cursor=frame.cursor, frame=frame)
            case "state":
                payload = parse_json_object(event.data, "Process state event payload must be an object")
                cursor = payload.get("cursor")
                if not isinstance(cursor, str):
                    raise DaytonaError("Process state event payload must include cursor")
                process = ToolboxProcessRecord.from_dict(payload)
                if process is None:
                    raise DaytonaError("Process state event payload is missing")
                return ProcessStreamStateEvent(cursor=cursor, process=process)
            case "warning":
                payload = parse_json_object(event.data, "Process warning event payload must be an object")
                cursor = payload.get("cursor")
                message = payload.get("message")
                first_available_cursor = payload.get("firstAvailableCursor")
                if (
                    not isinstance(cursor, str)
                    or not isinstance(message, str)
                    or not isinstance(first_available_cursor, str)
                ):
                    raise DaytonaError("Process warning event payload is invalid")
                return ProcessStreamWarningEvent(
                    cursor=cursor,
                    message=message,
                    first_available_cursor=first_available_cursor,
                )
            case "eof":
                payload = parse_json_object(event.data, "Process EOF event payload must be an object")
                cursor = payload.get("cursor")
                if not isinstance(cursor, str):
                    raise DaytonaError("Process EOF event payload must include cursor")
                return ProcessStreamEofEvent(cursor=cursor)
            case _:
                raise DaytonaError(f"Unknown process log event: {event.event}")

    def _invoke_process(
        self,
        operation: Callable[..., _ProcessResultT],
        **kwargs: object,
    ) -> _ProcessResultT:
        try:
            return operation(**kwargs)
        except ToolboxApiException as exc:
            raw_status = cast(object, exc.status)
            raw_headers = cast(object, exc.headers)
            raw_body = cast(object, exc.body)
            headers: dict[str, str] | None = None
            if isinstance(raw_headers, Mapping):
                header_items = cast(Mapping[object, object], raw_headers)
                headers = {str(key): str(value) for key, value in header_items.items()}
            raise process_error_from_response(
                status_code=raw_status if isinstance(raw_status, int) else None,
                headers=headers,
                body=raw_body if isinstance(raw_body, str) else None,
                fallback_message=str(exc),
            ) from exc

    @intercept_errors(message_prefix="Failed to create session: ")
    @with_instrumentation()
    def create_session(self, session_id: str, request_timeout: float | None = None) -> None:
        """Creates a new long-running background session in the Sandbox.

        Sessions are background processes that maintain state between commands, making them ideal for
        scenarios requiring multiple related commands or persistent environment setup. You can run
        long-running commands and monitor process status.

        Args:
            session_id (str): Unique identifier for the new session.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            # Create a new session
            session_id = "my-session"
            sandbox.process.create_session(session_id)
            session = sandbox.process.get_session(session_id)
            # Do work...
            sandbox.process.delete_session(session_id)
            ```
        """
        request = CreateSessionRequest(session_id=session_id)
        self._api_client.create_session(request=request, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to get session: ")
    def get_session(self, session_id: str, request_timeout: float | None = None) -> Session:
        """Gets a session in the Sandbox.

        Args:
            session_id (str): Unique identifier of the session to retrieve.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            Session: Session information including:
                - session_id: The session's unique identifier
                - commands: List of commands executed in the session

        Example:
            ```python
            session = sandbox.process.get_session("my-session")
            for cmd in session.commands:
                print(f"Command: {cmd.command}")
            ```
        """
        try:
            return self._api_client.get_session(session_id=session_id, _request_timeout=http_timeout(request_timeout))
        except ValidationError:
            return self._get_legacy_session(session_id=session_id, request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to get sandbox entrypoint session: ")
    def get_entrypoint_session(self, request_timeout: float | None = None) -> Session:
        """Gets the sandbox entrypoint session.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            Session: Entrypoint session information including:
                - session_id: The entrypoint session's unique identifier
                - commands: List of commands executed in the entrypoint session

        Example:
            ```python
            session = sandbox.process.get_entrypoint_session()
            for cmd in session.commands:
                print(f"Command: {cmd.command}")
            ```
        """
        try:
            return self._api_client.get_entrypoint_session(_request_timeout=http_timeout(request_timeout))
        except ValidationError:
            return self._get_legacy_entrypoint_session(request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to get session command: ")
    @with_instrumentation()
    def get_session_command(self, session_id: str, command_id: str, request_timeout: float | None = None) -> Command:
        """Gets information about a specific command executed in a session.

        Args:
            session_id (str): Unique identifier of the session.
            command_id (str): Unique identifier of the command.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            Command: Command information including:
                - id: The command's unique identifier
                - command: The executed command string
                - exit_code: Command's exit status (if completed)

        Example:
            ```python
            cmd = sandbox.process.get_session_command("my-session", "cmd-123")
            if cmd.exit_code == 0:
                print(f"Command {cmd.command} completed successfully")
            ```
        """
        return self._api_client.get_session_command(
            session_id=session_id, command_id=command_id, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to execute session command: ")
    @with_instrumentation()
    def execute_session_command(
        self,
        session_id: str,
        req: SessionExecuteRequest,
        timeout: int | None = None,
    ) -> SessionExecuteResponse:
        """Executes a command in the session.

        Args:
            session_id (str): Unique identifier of the session to use.
            req (SessionExecuteRequest): Command execution request containing:
                - command: The command to execute
                - run_async: Whether to execute asynchronously

        Returns:
            SessionExecuteResponse: Command execution results containing:
                - cmd_id: Unique identifier for the executed command
                - output: Combined command output (stdout and stderr) (if synchronous execution)
                - stdout: Standard output from the command
                - stderr: Standard error from the command
                - exit_code: Command exit status (if synchronous execution)

        Example:
            ```python
            # Execute commands in sequence, maintaining state
            session_id = "my-session"

            # Change directory
            req = SessionExecuteRequest(command="cd /workspace")
            sandbox.process.execute_session_command(session_id, req)

            # Create a file
            req = SessionExecuteRequest(command="echo 'Hello' > test.txt")
            sandbox.process.execute_session_command(session_id, req)

            # Read the file
            req = SessionExecuteRequest(command="cat test.txt")
            result = sandbox.process.execute_session_command(session_id, req)
            print(f"Command stdout: {result.stdout}")
            print(f"Command stderr: {result.stderr}")
            ```
        """
        response = self._api_client.session_execute_command(
            session_id=session_id,
            request=req,
            _request_timeout=http_timeout(timeout + 5 if timeout else None),
        )

        return SessionExecuteResponse.model_construct(
            cmd_id=response.cmd_id,
            output=response.output,
            stdout=response.stdout or "",
            stderr=response.stderr or "",
            exit_code=response.exit_code,
            additional_properties=response.additional_properties,
        )

    @intercept_errors(message_prefix="Failed to get session command logs: ")
    @with_instrumentation()
    def get_session_command_logs(
        self, session_id: str, command_id: str, request_timeout: float | None = None
    ) -> SessionCommandLogsResponse:
        """Get the logs for a command executed in a session.

        Args:
            session_id (str): Unique identifier of the session.
            command_id (str): Unique identifier of the command.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            SessionCommandLogsResponse: Command logs including:
                - output: Combined command output (stdout and stderr)
                - stdout: Standard output from the command
                - stderr: Standard error from the command

        Example:
            ```python
            logs = sandbox.process.get_session_command_logs(
                "my-session",
                "cmd-123"
            )
            print(f"Command stdout: {logs.stdout}")
            print(f"Command stderr: {logs.stderr}")
            ```
        """
        response = self._api_client.get_session_command_logs(
            session_id=session_id, command_id=command_id, _request_timeout=http_timeout(request_timeout)
        )

        return SessionCommandLogsResponse(output=response.output, stdout=response.stdout, stderr=response.stderr)

    @intercept_errors(message_prefix="Failed to get session command logs: ")
    async def get_session_command_logs_async(
        self, session_id: str, command_id: str, on_stdout: OutputHandler[str], on_stderr: OutputHandler[str]
    ) -> None:
        """Asynchronously retrieves and processes the logs for a command executed in a session as they become available.

        Accepts both sync and async callbacks. Async callbacks are awaited.
        Blocking synchronous operations inside callbacks may cause WebSocket
        disconnections — use async callbacks and async libraries to avoid this.

        Args:
            session_id (str): Unique identifier of the session.
            command_id (str): Unique identifier of the command.
            on_stdout (OutputHandler[str]): Callback function to handle stdout log chunks as they arrive.
            on_stderr (OutputHandler[str]): Callback function to handle stderr log chunks as they arrive.

        Example:
            ```python
            await sandbox.process.get_session_command_logs_async(
                "my-session",
                "cmd-123",
                lambda log: print(f"[STDOUT]: {log}"),
                lambda log: print(f"[STDERR]: {log}"),
            )
            ```
        """

        _, url, headers, *_ = self._api_client._get_session_command_logs_serialize(
            session_id=session_id,
            command_id=command_id,
            follow=True,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )

        url = re.sub(r"^http", "ws", url)

        await self._consume_log_websocket(url, headers, on_stdout, on_stderr)

    @intercept_errors(message_prefix="Failed to get entrypoint logs: ")
    @with_instrumentation()
    def get_entrypoint_logs(self, request_timeout: float | None = None) -> SessionCommandLogsResponse:
        """Get the logs for the entrypoint session.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            SessionCommandLogsResponse: Command logs including:
                - output: Combined command output (stdout and stderr)
                - stdout: Standard output from the command
                - stderr: Standard error from the command

        Example:
            ```python
            logs = sandbox.process.get_entrypoint_logs()
            print(f"Command stdout: {logs.stdout}")
            print(f"Command stderr: {logs.stderr}")
            ```
        """
        response = self._api_client.get_entrypoint_logs(_request_timeout=http_timeout(request_timeout))

        return SessionCommandLogsResponse(output=response.output, stdout=response.stdout, stderr=response.stderr)

    @intercept_errors(message_prefix="Failed to get entrypoint logs: ")
    async def get_entrypoint_logs_async(self, on_stdout: OutputHandler[str], on_stderr: OutputHandler[str]) -> None:
        """Asynchronously retrieves and processes the logs for the entrypoint session as they become available.

        Args:
            on_stdout OutputHandler[str]: Callback function to handle stdout log chunks as they arrive.
            on_stderr OutputHandler[str]: Callback function to handle stderr log chunks as they arrive.

        Example:
            ```python
            await sandbox.process.get_entrypoint_logs_async(
                lambda log: print(f"[STDOUT]: {log}"),
                lambda log: print(f"[STDERR]: {log}"),
            )
            ```
        """

        _, url, headers, *_ = self._api_client._get_entrypoint_logs_serialize(
            follow=True,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )

        url = re.sub(r"^http", "ws", url)

        await self._consume_log_websocket(url, headers, on_stdout, on_stderr)

    @intercept_errors(message_prefix="Failed to send session command input: ")
    def send_session_command_input(
        self, session_id: str, command_id: str, data: str, request_timeout: float | None = None
    ) -> None:
        """Sends input data to a command executed in a session.

        Args:
            session_id (str): Unique identifier of the session.
            command_id (str): Unique identifier of the command.
            data (str): Input data to send.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.
        """
        self._api_client.send_input(
            session_id=session_id,
            command_id=command_id,
            request=SessionSendInputRequest(data=data),
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to list sessions: ")
    @with_instrumentation()
    def list_sessions(self, request_timeout: float | None = None) -> list[Session]:
        """Lists all sessions in the Sandbox.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            list[Session]: List of all sessions in the Sandbox.

        Example:
            ```python
            sessions = sandbox.process.list_sessions()
            for session in sessions:
                print(f"Session {session.session_id}:")
                print(f"  Commands: {len(session.commands)}")
            ```
        """
        try:
            return self._api_client.list_sessions(_request_timeout=http_timeout(request_timeout))
        except ValidationError:
            return self._list_legacy_sessions(request_timeout=request_timeout)

    def _get_legacy_session(
        self,
        *,
        session_id: str,
        request_timeout: float | None,
    ) -> Session:
        _, url, headers, *_ = self._api_client._get_session_serialize(
            session_id=session_id,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        return self._fetch_legacy_session(url=url, headers=headers, request_timeout=request_timeout)

    def _get_legacy_entrypoint_session(self, *, request_timeout: float | None) -> Session:
        _, url, headers, *_ = self._api_client._get_entrypoint_session_serialize(
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        return self._fetch_legacy_session(url=url, headers=headers, request_timeout=request_timeout)

    def _list_legacy_sessions(self, *, request_timeout: float | None) -> list[Session]:
        _, url, headers, *_ = self._api_client._list_sessions_serialize(
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        response = self._http_client.get(url, headers=headers, timeout=http_timeout(request_timeout))
        body = response.text
        payload: object = json.loads(body)
        if not isinstance(payload, list):
            raise DaytonaError("Session list response must be an array")
        items = cast(list[object], payload)
        return [self._deserialize_legacy_session_payload(item) for item in items]

    def _fetch_legacy_session(
        self,
        *,
        url: str,
        headers: dict[str, str],
        request_timeout: float | None,
    ) -> Session:
        response = self._http_client.get(url, headers=headers, timeout=http_timeout(request_timeout))
        return self._deserialize_legacy_session_body(response.text)

    def _deserialize_legacy_session_body(self, body: str) -> Session:
        payload: object = json.loads(body)
        return self._deserialize_legacy_session_payload(payload)

    def _deserialize_legacy_session_payload(self, payload: object) -> Session:
        if not isinstance(payload, dict):
            raise DaytonaError("Session response must be an object")
        payload_map = cast(dict[str, object], payload)
        session_id = payload_map.get("sessionId")
        commands_payload = payload_map.get("commands")
        if not isinstance(session_id, str):
            raise DaytonaError("Session response must include sessionId")
        if commands_payload is None:
            commands_payload = []
        if not isinstance(commands_payload, list):
            raise DaytonaError("Session response must include commands")

        commands: list[Command] = []
        for command_payload in cast(list[object], commands_payload):
            if not isinstance(command_payload, dict):
                raise DaytonaError("Session command payload must be an object")
            command = Command.from_dict(cast(dict[str, object], command_payload))
            if command is None:
                raise DaytonaError("Session command payload is missing")
            commands.append(command)
        return Session(commands=commands, session_id=session_id)

    @intercept_errors(message_prefix="Failed to delete session: ")
    @with_instrumentation()
    def delete_session(self, session_id: str, request_timeout: float | None = None) -> None:
        """Terminates and removes a session from the Sandbox, cleaning up any resources
        associated with it.

        Args:
            session_id (str): Unique identifier of the session to delete.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Example:
            ```python
            # Create and use a session
            sandbox.process.create_session("temp-session")
            # ... use the session ...

            # Clean up when done
            sandbox.process.delete_session("temp-session")
            ```
        """
        self._api_client.delete_session(session_id=session_id, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to create PTY session: ")
    @with_instrumentation()
    def create_pty_session(
        self,
        id: str,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        pty_size: PtySize | None = None,
    ) -> PtyHandle:
        """Creates a new PTY (pseudo-terminal) session in the Sandbox.

        Creates an interactive terminal session that can execute commands and handle user input.
        The PTY session behaves like a real terminal, supporting features like command history.

        Args:
            id: Unique identifier for the PTY session. Must be unique within the Sandbox.
            cwd: Working directory for the PTY session. Defaults to the sandbox's working directory.
            env: Environment variables to set in the PTY session. These will be merged with
                the Sandbox's default environment variables.
            pty_size: Terminal size configuration. Defaults to 80x24 if not specified.

        Returns:
            PtyHandle: Handle for managing the created PTY session. Use this to send input,
                           receive output, resize the terminal, and manage the session lifecycle.

        Raises:
            DaytonaError: If the PTY session creation fails or the session ID is already in use.
        """
        cols = pty_size.cols if pty_size else 80
        rows = pty_size.rows if pty_size else 24

        # Build query params for the combined create-connect endpoint (single round-trip)
        params: dict[str, str] = {
            "id": id,
            "cols": str(cols),
            "rows": str(rows),
        }
        if cwd:
            params["cwd"] = cwd

        # Derive the WS URL from the API client's base configuration
        _, base_url, headers, *_ = self._api_client._connect_pty_session_serialize(
            session_id="__placeholder__",
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        url = base_url.replace("/process/pty/__placeholder__/connect", "/process/pty/create-connect")
        url = re.sub(r"^http", "ws", url)
        url = f"{url}?{urlencode(params)}"

        # Envs travel as a WebSocket subprotocol token (base64url-no-pad of the JSON object)
        # rather than the query string or a header, keeping the transport uniform across
        # runtimes and potentially-large/secret values out of URLs and access logs.
        subprotocols: list[str] = [PTY_EXIT_CONTROL_SUBPROTOCOL]
        if envs:
            encoded = base64.urlsafe_b64encode(json.dumps(envs).encode()).rstrip(b"=").decode()
            subprotocols.append(f"X-Daytona-Pty-Envs~{encoded}")

        try:
            ws_cm = httpx_ws.connect_ws(url, self._http_client, headers=headers, subprotocols=subprotocols)
            ws = ws_cm.__enter__()  # pylint: disable=unnecessary-dunder-call
        except httpx_ws.WebSocketUpgradeError as e:
            # A failed WS upgrade carries the HTTP response; surface it as the matching typed
            # Daytona exception (e.g. 404 -> DaytonaNotFoundError, 409 -> DaytonaConflictError)
            # so callers can branch on it like any REST error (parity with the async path).
            raise create_daytona_error(
                f"WebSocket upgrade failed with HTTP {e.response.status_code}",
                status_code=e.response.status_code,
                headers=e.response.headers,
            ) from e

        def resize_handler(pty_size_arg: PtySize) -> PtySessionInfo:
            return self.resize_pty_session(id, pty_size_arg)

        def kill_handler() -> None:
            self.kill_pty_session(id)

        # Guard from here so a failure constructing the handle or completing the
        # handshake closes the socket instead of leaking the WebSocket connection.
        handle: PtyHandle | None = None
        try:
            handle = PtyHandle(
                ws,
                session_id=id,
                handle_resize=resize_handler,
                handle_kill=kill_handler,
                ws_context_manager=ws_cm,
            )
            handle.wait_for_connection()
        except BaseException:
            if handle is not None:
                handle.disconnect()
            else:
                _ = ws_cm.__exit__(None, None, None)  # pylint: disable=unnecessary-dunder-call
            raise
        return handle

    @intercept_errors(message_prefix="Failed to connect PTY session: ")
    @with_instrumentation()
    def connect_pty_session(
        self,
        session_id: str,
    ) -> PtyHandle:
        """Connects to an existing PTY session in the Sandbox.

        Establishes a WebSocket connection to an existing PTY session, allowing you to
        interact with a previously created terminal session.

        Args:
            session_id: Unique identifier of the PTY session to connect to.

        Returns:
            PtyHandle: Handle for managing the connected PTY session.

        Raises:
            DaytonaError: If the PTY session doesn't exist or connection fails.
        """
        _, url, headers, *_ = self._api_client._connect_pty_session_serialize(
            session_id=session_id,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        url = re.sub(r"^http", "ws", url)

        # Long-lived PTY: open without context manager so PtyHandle owns the lifecycle and
        # closes via handle.disconnect(). The WS upgrade pulls a TCP/TLS connection from
        # self._http_client's pool (shared TLS context, DNS cache) — once upgraded, that
        # socket is dedicated to this PTY for its entire lifetime.
        ws_cm = httpx_ws.connect_ws(
            url, self._http_client, headers=headers, subprotocols=[PTY_EXIT_CONTROL_SUBPROTOCOL]
        )
        ws = ws_cm.__enter__()  # pylint: disable=unnecessary-dunder-call

        def resize_handler(pty_size: PtySize) -> PtySessionInfo:
            return self.resize_pty_session(session_id, pty_size)

        def kill_handler() -> None:
            self.kill_pty_session(session_id)

        handle = PtyHandle(
            ws,
            session_id=session_id,
            handle_resize=resize_handler,
            handle_kill=kill_handler,
            ws_context_manager=ws_cm,
        )
        try:
            handle.wait_for_connection()
        except BaseException:
            handle.disconnect()
            raise
        return handle

    @intercept_errors(message_prefix="Failed to list PTY sessions: ")
    @with_instrumentation()
    def list_pty_sessions(self, request_timeout: float | None = None) -> list[PtySessionInfo]:
        """Lists all PTY sessions in the Sandbox.

        Retrieves information about all PTY sessions in this Sandbox.

        Args:
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            list[PtySessionInfo]: List of PTY session information objects containing
                                details about each session's state, creation time, and configuration.

        Example:
            ```python
            # List all PTY sessions
            sessions = sandbox.process.list_pty_sessions()

            for session in sessions:
                print(f"Session ID: {session.id}")
                print(f"Active: {session.active}")
                print(f"Created: {session.created_at}")
            ```
        """
        return (self._api_client.list_pty_sessions(_request_timeout=http_timeout(request_timeout))).sessions

    @intercept_errors(message_prefix="Failed to get PTY session info: ")
    @with_instrumentation()
    def get_pty_session_info(self, session_id: str, request_timeout: float | None = None) -> PtySessionInfo:
        """Gets detailed information about a specific PTY session.

        Retrieves comprehensive information about a PTY session including its current state,
        configuration, and metadata.

        Args:
            session_id: Unique identifier of the PTY session to retrieve information for.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            PtySessionInfo: Detailed information about the PTY session including ID, state,
                           creation time, working directory, environment variables, and more.

        Raises:
            DaytonaError: If the PTY session doesn't exist.

        Example:
            ```python
            # Get details about a specific PTY session
            session_info = sandbox.process.get_pty_session_info("my-session")

            print(f"Session ID: {session_info.id}")
            print(f"Active: {session_info.active}")
            print(f"Working Directory: {session_info.cwd}")
            print(f"Terminal Size: {session_info.cols}x{session_info.rows}")
            ```
        """
        return self._api_client.get_pty_session(session_id=session_id, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to kill PTY session: ")
    @with_instrumentation()
    def kill_pty_session(self, session_id: str, request_timeout: float | None = None) -> None:
        """Kills a PTY session and terminates its associated process.

        Forcefully terminates the PTY session and cleans up all associated resources.
        This will close any active connections and kill the underlying shell process.
        This operation is irreversible. Any unsaved work in the terminal session will be lost.

        Args:
            session_id: Unique identifier of the PTY session to kill.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Raises:
            DaytonaError: If the PTY session doesn't exist or cannot be killed.

        Example:
            ```python
            # Kill a specific PTY session
            sandbox.process.kill_pty_session("my-session")

            # Verify the session no longer exists
            pty_sessions = sandbox.process.list_pty_sessions()
            for pty_session in pty_sessions:
                print(f"PTY session: {pty_session.id}")
            ```
        """
        _ = self._api_client.delete_pty_session(session_id=session_id, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to resize PTY session: ")
    @with_instrumentation()
    def resize_pty_session(
        self, session_id: str, pty_size: PtySize, request_timeout: float | None = None
    ) -> PtySessionInfo:
        """Resizes a PTY session's terminal dimensions.

        Changes the terminal size of an active PTY session. This is useful when the
        client terminal is resized or when you need to adjust the display for different
        output requirements.

        Args:
            session_id: Unique identifier of the PTY session to resize.
            pty_size: New terminal dimensions containing the desired columns and rows.
            request_timeout (float | None): Optional client-side request timeout in seconds. Client-side
                only. It bounds how long the SDK waits for the HTTP response and does not cancel
                the operation on the server. Positive values under 1 second are rounded up to 1
                second; 0 disables the client-side timeout and negative values are rejected.

        Returns:
            PtySessionInfo: Updated session information reflecting the new terminal size.

        Raises:
            DaytonaError: If the PTY session doesn't exist or resize operation fails.

        Example:
            ```python
            from daytona.common.pty import PtySize

            # Resize a PTY session to a larger terminal
            new_size = PtySize(rows=40, cols=150)
            updated_info = sandbox.process.resize_pty_session("my-session", new_size)

            print(f"Terminal resized to {updated_info.cols}x{updated_info.rows}")

            # You can also use the PtyHandle's resize method
            pty_handle.resize(new_size)
            ```
        """
        return self._api_client.resize_pty_session(
            session_id=session_id,
            request=PtyResizeRequest(cols=pty_size.cols, rows=pty_size.rows),
            _request_timeout=http_timeout(request_timeout),
        )
