# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import TypeVar
from urllib.parse import urlencode

import aiohttp
from pydantic import ValidationError

from daytona_toolbox_api_client_async import (
    CodeRunRequest,
    Command,
    CreateProcessRequest,
    CreateSessionRequest,
    ExecuteRequest,
    KillProcessRequest,
    Process as ToolboxProcessRecord,
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
from daytona_toolbox_api_client_async.exceptions import OpenApiException as ToolboxOpenApiException

from .._utils.errors import intercept_errors
from .._utils.otel_decorator import with_instrumentation
from .._utils.stream import std_demux_stream_aio
from .._utils.timeout import http_timeout
from ..common.charts import parse_chart
from ..common.errors import (
    SOURCE_DAEMON,
    DaytonaDaemonUpgradeRequiredError,
    DaytonaError,
    DaytonaUnsupportedOperationError,
    DaytonaValidationError,
    create_daytona_error,
)
from ..common.process import (
    CodeRunParams,
    ExecuteResponse,
    ExecutionArtifacts,
    LegacySessionResponse,
    OutputHandler,
    ProcessHandleJSON,
    ProcessKeepLogsName,
    ProcessKindName,
    ProcessLogEncoding,
    ProcessStateFilter,
    ProcessStdinModeName,
    SessionCommandLogsResponse,
    SessionExecuteRequest,
    SessionExecuteResponse,
)
from ..common.pty import PTY_EXIT_CONTROL_SUBPROTOCOL, PtySize
from ..handle.async_process_handle import (
    AsyncProcessHandle,
    ProcessStreamEofEvent,
    ProcessStreamEvent,
    ProcessStreamLogEvent,
    ProcessStreamStateEvent,
    ProcessStreamWarningEvent,
)
from ..handle.async_pty_handle import AsyncPtyHandle
from ..internal.process_v2 import (
    CreateProcessPayload,
    KillProcessPayload,
    ProcessStdinPayload,
    ResizeProcessPayload,
    decode_process_handle_json,
    normalize_process_stdin,
    process_v2_error_from_response,
    pty_result_from_process_result,
    should_mark_process_v2_supported,
)
from ..internal.sse import ServerSentEvent, ServerSentEventParser
from ..internal.shared_session import http_session_of

_ProcessV2ResultT = TypeVar("_ProcessV2ResultT")


class AsyncProcess:
    """Handles process and code execution within a Sandbox."""

    def __init__(
        self,
        language: str,
        api_client: ProcessApi,
        sandbox_id: str = "",
    ):
        """Initialize a new Process instance.

        Args:
            api_client (ProcessApi): API client for process operations.
        """
        self._language: str = language
        self._api_client: ProcessApi = api_client
        self._sandbox_id: str = sandbox_id
        self._process_v2_supported: bool | None = None
        self._process_v2_support_error: DaytonaDaemonUpgradeRequiredError | None = None

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    async def _open_ws(
        self,
        url: str,
        headers: dict[str, str],
        subprotocols: list[str] | None = None,
    ) -> "aiohttp.ClientWebSocketResponse[bool]":
        return await http_session_of(self._api_client.api_client).ws_connect(
            url, headers=headers, protocols=subprotocols or ()
        )

    async def _consume_log_websocket(
        self,
        url: str,
        headers: dict[str, str],
        on_stdout: OutputHandler[str],
        on_stderr: OutputHandler[str],
    ) -> None:
        """Open a log-streaming WebSocket, demultiplex stdout/stderr until EOF, then close."""
        ws = await self._open_ws(url, headers)
        try:
            await std_demux_stream_aio(ws, on_stdout, on_stderr)
        finally:
            if not ws.closed:
                _ = await ws.close()

    @intercept_errors(message_prefix="Failed to execute command: ")
    @with_instrumentation()
    async def exec(
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
            response = await sandbox.process.exec("echo 'Hello'")
            print(response.artifacts.stdout)  # Prints: Hello

            # Command with working directory
            result = await sandbox.process.exec("ls", cwd="workspace/src")

            # Command with timeout
            result = await sandbox.process.exec("sleep 10", timeout=5)
            ```
        """
        execute_request = ExecuteRequest(command=command, cwd=cwd, timeout=timeout, envs=env)

        response = await self._api_client.execute_command(
            request=execute_request,
            _request_timeout=http_timeout(timeout + 5 if timeout else None),
        )

        result = response.result or ""
        artifacts = ExecutionArtifacts(stdout=result, charts=[])

        # TODO: Remove model_construct once everything is migrated to pydantic # pylint: disable=fixme
        return ExecuteResponse.model_construct(
            exit_code=(
                response.exit_code if response.exit_code is not None else response.additional_properties.get("code")
            ),
            result=result,
            artifacts=artifacts,
            additional_properties=response.additional_properties,
        )

    @with_instrumentation()
    async def code_run(
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
            response = await sandbox.process.code_run('''
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

            response = await sandbox.process.code_run(code)
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

        response = await self._api_client.code_run(
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
    async def start(
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
    ) -> AsyncProcessHandle:
        process = await self._create_process(
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
        return AsyncProcessHandle(self, process.id)

    @intercept_errors(message_prefix="Failed to run process: ")
    @with_instrumentation()
    async def run(
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
    ) -> ProcessResult:
        process = await self._create_process(
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
        return await self._wait_for_process(process.id, timeout_ms=wait_timeout_ms)

    @intercept_errors(message_prefix="Failed to get process: ")
    @with_instrumentation()
    async def get(self, id: str) -> AsyncProcessHandle:
        _ = await self._get_process_record(id)
        return AsyncProcessHandle(self, id)

    @intercept_errors(message_prefix="Failed to list processes: ")
    @with_instrumentation()
    async def list(
        self,
        *,
        state: ProcessStateFilter | None = None,
        kind: ProcessKindName | None = None,
        session_id: str | None = None,
        name: str | None = None,
        pid: int | None = None,
    ) -> list[AsyncProcessHandle]:
        processes = await self._invoke_process_v2(
            self._api_client.list_processes_v2,
            state=state,
            kind=kind,
            session_id=session_id,
            name=name,
            pid=pid,
        )
        return [AsyncProcessHandle(self, process.id) for process in processes]

    @intercept_errors(message_prefix="Failed to rehydrate process handle: ")
    def from_json(self, data: ProcessHandleJSON | Mapping[str, str]) -> AsyncProcessHandle:
        sandbox_id, process_id = decode_process_handle_json(data)
        if self._sandbox_id and sandbox_id != self._sandbox_id:
            raise DaytonaValidationError(
                f"Serialized process handle belongs to sandbox {sandbox_id}, current sandbox is {self._sandbox_id}",
            )
        return AsyncProcessHandle(self, process_id)

    async def _create_process(
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
            terminal_payload: dict[str, int | str] = {"cols": terminal.cols, "rows": terminal.rows}
            if term is not None:
                terminal_payload["term"] = term
            payload["terminal"] = terminal_payload
        if keep_logs is not None:
            payload["keep_logs"] = keep_logs

        request = CreateProcessRequest.model_validate(payload)
        return await self._invoke_process_v2(self._api_client.create_process_v2, request=request)

    async def _get_process_record(self, process_id: str) -> ToolboxProcessRecord:
        return await self._invoke_process_v2(self._api_client.get_process_v2, id=process_id)

    async def _get_process_logs(
        self,
        process_id: str,
        *,
        cursor: str | None,
        limit: int | None,
        encoding: ProcessLogEncoding,
    ) -> ProcessLogPage:
        return await self._invoke_process_v2(
            self._api_client.get_process_logs_v2,
            id=process_id,
            cursor=cursor,
            limit=limit,
            encoding=encoding,
        )

    async def _stream_process_logs(self, process_id: str, *, cursor: str | None) -> AsyncIterator[ProcessStreamEvent]:
        if self._process_v2_support_error is not None:
            raise self._process_v2_support_error

        _, url, headers, *_ = self._api_client._get_process_logs_v2_serialize(
            id=process_id,
            cursor=cursor,
            limit=None,
            encoding="text",
            follow=True,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        stream_headers = dict(headers)
        stream_headers["Accept"] = "text/event-stream"

        parser = ServerSentEventParser()
        http_session = http_session_of(self._api_client.api_client)
        async with http_session.get(url, headers=stream_headers, timeout=None) as response:
            if response.status >= 400:
                body = await response.text()
                error = process_v2_error_from_response(
                    status_code=response.status,
                    headers=dict(response.headers),
                    body=body,
                    fallback_message=body,
                )
                self._remember_process_v2_error(error)
                raise error

            self._process_v2_supported = True
            async for raw_line in response.content:
                line = raw_line.decode("utf-8")
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
            yield self._deserialize_stream_event(final_event)

    async def _send_process_stdin(self, process_id: str, data: str | bytes) -> None:
        payload: ProcessStdinPayload = {"data": normalize_process_stdin(data)}
        request = ProcessStdinRequest.model_validate(payload)
        _ = await self._invoke_process_v2(self._api_client.send_process_stdin_v2, id=process_id, request=request)

    async def _send_process_stdin_eof(self, process_id: str) -> None:
        payload: ProcessStdinPayload = {"eof": True}
        request = ProcessStdinRequest.model_validate(payload)
        _ = await self._invoke_process_v2(self._api_client.send_process_stdin_v2, id=process_id, request=request)

    async def _signal_process(
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
        _ = await self._invoke_process_v2(self._api_client.signal_process_v2, id=process_id, request=request)

    async def _resize_process(self, process_id: str, *, cols: int, rows: int) -> None:
        payload: ResizeProcessPayload = {"cols": cols, "rows": rows}
        request = ResizeProcessRequest.model_validate(payload)
        _ = await self._invoke_process_v2(self._api_client.resize_process_v2, id=process_id, request=request)

    async def _wait_for_process(self, process_id: str, *, timeout_ms: int | None = None) -> ProcessResult:
        return await self._invoke_process_v2(self._api_client.wait_for_process_v2, id=process_id, timeout_ms=timeout_ms)

    async def _attach_process_terminal(self, process_id: str) -> AsyncPtyHandle:
        process = await self._get_process_record(process_id)
        if process.kind != "pty":
            raise DaytonaUnsupportedOperationError(
                "attach is only supported for kind=pty processes",
                status_code=400,
                code="UNSUPPORTED_OPERATION",
                source=SOURCE_DAEMON,
            )

        _, url, headers, *_ = self._api_client._attach_process_v2_serialize(
            id=process_id,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        ws_url = re.sub(r"^http", "ws", url)
        try:
            ws = await self._open_ws(ws_url, headers)
        except aiohttp.WSServerHandshakeError as e:
            raise create_daytona_error(
                f"WebSocket upgrade failed with HTTP {e.status}",
                status_code=e.status,
                headers=e.headers,
            ) from e

        async def resize_handler(pty_size: PtySize) -> PtySessionInfo:
            await self._resize_process(process_id, cols=pty_size.cols, rows=pty_size.rows)
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

        async def kill_handler() -> None:
            await self._signal_process(process_id, signal="SIGTERM", escalate_after_ms=None, escalate_to="SIGKILL")

        async def result_resolver() -> PtyResult:
            result = await self._wait_for_process(process_id)
            return pty_result_from_process_result(
                exit_code=result.exit_code,
                reason=result.reason,
                signal=result.signal,
            )

        return AsyncPtyHandle(
            ws,
            session_id=process_id,
            handle_resize=resize_handler,
            handle_kill=kill_handler,
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
                payload = json.loads(event.data)
                if not isinstance(payload, dict):
                    raise DaytonaError("Process state event payload must be an object")
                cursor = payload.get("cursor")
                if not isinstance(cursor, str):
                    raise DaytonaError("Process state event payload must include cursor")
                process = ToolboxProcessRecord.from_dict(payload)
                if process is None:
                    raise DaytonaError("Process state event payload is missing")
                return ProcessStreamStateEvent(cursor=cursor, process=process)
            case "warning":
                payload = json.loads(event.data)
                if not isinstance(payload, dict):
                    raise DaytonaError("Process warning event payload must be an object")
                cursor = payload.get("cursor")
                message = payload.get("message")
                first_available_cursor = payload.get("firstAvailableCursor")
                if not isinstance(cursor, str) or not isinstance(message, str) or not isinstance(first_available_cursor, str):
                    raise DaytonaError("Process warning event payload is invalid")
                return ProcessStreamWarningEvent(
                    cursor=cursor,
                    message=message,
                    first_available_cursor=first_available_cursor,
                )
            case "eof":
                payload = json.loads(event.data)
                if not isinstance(payload, dict):
                    raise DaytonaError("Process EOF event payload must be an object")
                cursor = payload.get("cursor")
                if not isinstance(cursor, str):
                    raise DaytonaError("Process EOF event payload must include cursor")
                return ProcessStreamEofEvent(cursor=cursor)
            case _:
                raise DaytonaError(f"Unknown process log event: {event.event}")

    async def _invoke_process_v2(
        self,
        operation: Callable[..., Awaitable[_ProcessV2ResultT]],
        **kwargs: object,
    ) -> _ProcessV2ResultT:
        if self._process_v2_support_error is not None:
            raise self._process_v2_support_error

        try:
            result = await operation(**kwargs)
        except ToolboxOpenApiException as exc:
            headers = exc.headers if isinstance(exc.headers, Mapping) else None
            error = process_v2_error_from_response(
                status_code=exc.status,
                headers=headers,
                body=exc.body,
                fallback_message=str(exc),
            )
            self._remember_process_v2_error(error)
            raise error from exc

        self._process_v2_supported = True
        return result

    def _remember_process_v2_error(self, error: DaytonaError) -> None:
        if should_mark_process_v2_supported(error.status_code, error.source, error.code):
            self._process_v2_supported = True
            return
        if isinstance(error, DaytonaDaemonUpgradeRequiredError):
            self._process_v2_support_error = error

    @intercept_errors(message_prefix="Failed to create session: ")
    @with_instrumentation()
    async def create_session(self, session_id: str, request_timeout: float | None = None) -> None:
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
            await sandbox.process.create_session(session_id)
            session = await sandbox.process.get_session(session_id)
            # Do work...
            await sandbox.process.delete_session(session_id)
            ```
        """
        request = CreateSessionRequest(session_id=session_id)
        await self._api_client.create_session(request=request, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to get session: ")
    async def get_session(
        self, session_id: str, request_timeout: float | None = None
    ) -> LegacySessionResponse[Command] | Session:
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
            session = await sandbox.process.get_session("my-session")
            for cmd in session.commands:
                print(f"Command: {cmd.command}")
            ```
        """
        try:
            return await self._api_client.get_session(
                session_id=session_id,
                _request_timeout=http_timeout(request_timeout),
            )
        except ValidationError:
            return await self._get_legacy_session(session_id=session_id, request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to get sandbox entrypoint session: ")
    async def get_entrypoint_session(
        self, request_timeout: float | None = None
    ) -> LegacySessionResponse[Command] | Session:
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
            session = await sandbox.process.get_entrypoint_session()
            for cmd in session.commands:
                print(f"Command: {cmd.command}")
            ```
        """
        try:
            return await self._api_client.get_entrypoint_session(_request_timeout=http_timeout(request_timeout))
        except ValidationError:
            return await self._get_legacy_entrypoint_session(request_timeout=request_timeout)

    @intercept_errors(message_prefix="Failed to get session command: ")
    @with_instrumentation()
    async def get_session_command(
        self, session_id: str, command_id: str, request_timeout: float | None = None
    ) -> Command:
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
            cmd = await sandbox.process.get_session_command("my-session", "cmd-123")
            if cmd.exit_code == 0:
                print(f"Command {cmd.command} completed successfully")
            ```
        """
        return await self._api_client.get_session_command(
            session_id=session_id, command_id=command_id, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to execute session command: ")
    @with_instrumentation()
    async def execute_session_command(
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
            await sandbox.process.execute_session_command(session_id, req)

            # Create a file
            req = SessionExecuteRequest(command="echo 'Hello' > test.txt")
            await sandbox.process.execute_session_command(session_id, req)

            # Read the file
            req = SessionExecuteRequest(command="cat test.txt")
            result = await sandbox.process.execute_session_command(session_id, req)
            print(f"Command stdout: {result.stdout}")
            print(f"Command stderr: {result.stderr}")
            ```
        """
        response = await self._api_client.session_execute_command(
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
    async def get_session_command_logs(
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
            logs = await sandbox.process.get_session_command_logs(
                "my-session",
                "cmd-123"
            )
            print(f"Command stdout: {logs.stdout}")
            print(f"Command stderr: {logs.stderr}")
            ```
        """
        response = await self._api_client.get_session_command_logs(
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
    async def get_entrypoint_logs(self, request_timeout: float | None = None) -> SessionCommandLogsResponse:
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
            logs = await sandbox.process.get_entrypoint_logs()
            print(f"Command stdout: {logs.stdout}")
            print(f"Command stderr: {logs.stderr}")
            ```
        """
        response = await self._api_client.get_entrypoint_logs(_request_timeout=http_timeout(request_timeout))

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
    async def send_session_command_input(
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
        await self._api_client.send_input(
            session_id=session_id,
            command_id=command_id,
            request=SessionSendInputRequest(data=data),
            _request_timeout=http_timeout(request_timeout),
        )

    @intercept_errors(message_prefix="Failed to list sessions: ")
    @with_instrumentation()
    async def list_sessions(self, request_timeout: float | None = None) -> list[LegacySessionResponse[Command] | Session]:
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
            sessions = await sandbox.process.list_sessions()
            for session in sessions:
                print(f"Session {session.session_id}:")
                print(f"  Commands: {len(session.commands)}")
            ```
        """
        try:
            return await self._api_client.list_sessions(_request_timeout=http_timeout(request_timeout))
        except ValidationError:
            return await self._list_legacy_sessions(request_timeout=request_timeout)

    async def _get_legacy_session(
        self,
        *,
        session_id: str,
        request_timeout: float | None,
    ) -> LegacySessionResponse[Command]:
        _, url, headers, *_ = self._api_client._get_session_serialize(
            session_id=session_id,
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        return await self._fetch_legacy_session(url=url, headers=headers, request_timeout=request_timeout)

    async def _get_legacy_entrypoint_session(self, *, request_timeout: float | None) -> LegacySessionResponse[Command]:
        _, url, headers, *_ = self._api_client._get_entrypoint_session_serialize(
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        return await self._fetch_legacy_session(url=url, headers=headers, request_timeout=request_timeout)

    async def _list_legacy_sessions(self, *, request_timeout: float | None) -> list[LegacySessionResponse[Command]]:
        _, url, headers, *_ = self._api_client._list_sessions_serialize(
            _request_auth=None,
            _content_type=None,
            _headers=None,
            _host_index=None,
        )
        http_session = http_session_of(self._api_client.api_client)
        async with http_session.get(url, headers=headers, timeout=http_timeout(request_timeout)) as response:
            body = await response.text()
        payload = json.loads(body)
        if not isinstance(payload, list):
            raise DaytonaError("Session list response must be an array")
        return [self._deserialize_legacy_session_payload(item) for item in payload]

    async def _fetch_legacy_session(
        self,
        *,
        url: str,
        headers: dict[str, str],
        request_timeout: float | None,
    ) -> LegacySessionResponse[Command]:
        http_session = http_session_of(self._api_client.api_client)
        async with http_session.get(url, headers=headers, timeout=http_timeout(request_timeout)) as response:
            body = await response.text()
        return self._deserialize_legacy_session_body(body)

    def _deserialize_legacy_session_body(self, body: str) -> LegacySessionResponse[Command]:
        payload = json.loads(body)
        return self._deserialize_legacy_session_payload(payload)

    def _deserialize_legacy_session_payload(self, payload: object) -> LegacySessionResponse[Command]:
        if not isinstance(payload, dict):
            raise DaytonaError("Session response must be an object")
        session_id = payload.get("sessionId")
        commands_payload = payload.get("commands")
        if not isinstance(session_id, str):
            raise DaytonaError("Session response must include sessionId")
        if not isinstance(commands_payload, list):
            raise DaytonaError("Session response must include commands")

        commands: list[Command] = []
        for command_payload in commands_payload:
            if not isinstance(command_payload, dict):
                raise DaytonaError("Session command payload must be an object")
            command = Command.from_dict(command_payload)
            if command is None:
                raise DaytonaError("Session command payload is missing")
            commands.append(command)
        return LegacySessionResponse(session_id=session_id, commands=commands)

    @intercept_errors(message_prefix="Failed to delete session: ")
    @with_instrumentation()
    async def delete_session(self, session_id: str, request_timeout: float | None = None) -> None:
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
            await sandbox.process.create_session("temp-session")
            # ... use the session ...

            # Clean up when done
            await sandbox.process.delete_session("temp-session")
            ```
        """
        await self._api_client.delete_session(session_id=session_id, _request_timeout=http_timeout(request_timeout))

    @intercept_errors(message_prefix="Failed to create PTY session: ")
    @with_instrumentation()
    async def create_pty_session(
        self,
        id: str,
        on_data: Callable[[bytes], None] | Callable[[bytes], Awaitable[None]],
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        pty_size: PtySize | None = None,
    ) -> AsyncPtyHandle:
        """Creates a new PTY (pseudo-terminal) session in the Sandbox.

        Creates an interactive terminal session that can execute commands and handle user input.
        The PTY session behaves like a real terminal, supporting features like command history.

        Args:
            id: Unique identifier for the PTY session. Must be unique within the Sandbox.
            on_data (Callable[[bytes], None] | Callable[[bytes], Awaitable[None]]):
                Callback function to handle PTY output data.
            cwd: Working directory for the PTY session. Defaults to the sandbox's working directory.
            env: Environment variables to set in the PTY session. These will be merged with
                the Sandbox's default environment variables.
            pty_size: Terminal size configuration. Defaults to 80x24 if not specified.

        Returns:
            AsyncPtyHandle: Handle for managing the created PTY session. Use this to send input,
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
            ws = await self._open_ws(url, headers, subprotocols)
        except aiohttp.WSServerHandshakeError as e:
            # A failed WS upgrade carries the HTTP response status; surface it as the matching
            # typed Daytona exception (e.g. 404 -> DaytonaNotFoundError, 409 ->
            # DaytonaConflictError) so callers can branch on it like any REST error.
            status_code = e.status
            raise create_daytona_error(
                f"WebSocket upgrade failed with HTTP {status_code}",
                status_code=status_code,
                headers=e.headers,
            ) from e

        async def resize_handler(pty_size_arg: PtySize) -> PtySessionInfo:
            return await self.resize_pty_session(id, pty_size_arg)

        async def kill_handler() -> None:
            await self.kill_pty_session(id)

        handle = AsyncPtyHandle(
            ws,
            on_data,
            session_id=id,
            handle_resize=resize_handler,
            handle_kill=kill_handler,
        )
        try:
            await handle.wait_for_connection()
        except BaseException:
            await handle.disconnect()
            raise
        return handle

    @intercept_errors(message_prefix="Failed to connect PTY session: ")
    @with_instrumentation()
    async def connect_pty_session(
        self,
        session_id: str,
        on_data: Callable[[bytes], None] | Callable[[bytes], Awaitable[None]],
    ) -> AsyncPtyHandle:
        """Connects to an existing PTY session in the Sandbox.

        Establishes a WebSocket connection to an existing PTY session, allowing you to
        interact with a previously created terminal session.

        Args:
            session_id: Unique identifier of the PTY session to connect to.

        Returns:
            AsyncPtyHandle: Handle for managing the connected PTY session.

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

        ws = await self._open_ws(url, headers, [PTY_EXIT_CONTROL_SUBPROTOCOL])

        async def resize_handler(pty_size: PtySize) -> PtySessionInfo:
            return await self.resize_pty_session(session_id, pty_size)

        async def kill_handler() -> None:
            await self.kill_pty_session(session_id)

        handle = AsyncPtyHandle(
            ws,
            on_data,
            session_id=session_id,
            handle_resize=resize_handler,
            handle_kill=kill_handler,
        )
        # If wait_for_connection() raises (handshake timeout / server-side error / cancel),
        # the caller never sees the handle and cannot call disconnect(), so we MUST close
        # the websocket and the per-call session ourselves before propagating the error.
        try:
            await handle.wait_for_connection()
        except BaseException:
            await handle.disconnect()
            raise
        return handle

    @intercept_errors(message_prefix="Failed to list PTY sessions: ")
    @with_instrumentation()
    async def list_pty_sessions(self, request_timeout: float | None = None) -> list[PtySessionInfo]:
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
            sessions = await sandbox.process.list_pty_sessions()

            for session in sessions:
                print(f"Session ID: {session.id}")
                print(f"Active: {session.active}")
                print(f"Created: {session.created_at}")
            ```
        """
        return (await self._api_client.list_pty_sessions(_request_timeout=http_timeout(request_timeout))).sessions

    @intercept_errors(message_prefix="Failed to get PTY session info: ")
    @with_instrumentation()
    async def get_pty_session_info(self, session_id: str, request_timeout: float | None = None) -> PtySessionInfo:
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
            session_info = await sandbox.process.get_pty_session_info("my-session")

            print(f"Session ID: {session_info.id}")
            print(f"Active: {session_info.active}")
            print(f"Working Directory: {session_info.cwd}")
            print(f"Terminal Size: {session_info.cols}x{session_info.rows}")
            ```
        """
        return await self._api_client.get_pty_session(
            session_id=session_id, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to kill PTY session: ")
    @with_instrumentation()
    async def kill_pty_session(self, session_id: str, request_timeout: float | None = None) -> None:
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
            await sandbox.process.kill_pty_session("my-session")

            # Verify the session no longer exists
            pty_sessions = await sandbox.process.list_pty_sessions()
            for pty_session in pty_sessions:
                print(f"PTY session: {pty_session.id}")
            ```
        """
        _ = await self._api_client.delete_pty_session(
            session_id=session_id, _request_timeout=http_timeout(request_timeout)
        )

    @intercept_errors(message_prefix="Failed to resize PTY session: ")
    @with_instrumentation()
    async def resize_pty_session(
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
            updated_info = await sandbox.process.resize_pty_session("my-session", new_size)

            print(f"Terminal resized to {updated_info.cols}x{updated_info.rows}")

            # You can also use the AsyncPtyHandle's resize method
            await pty_handle.resize(new_size)
            ```
        """
        return await self._api_client.resize_pty_session(
            session_id=session_id,
            request=PtyResizeRequest(cols=pty_size.cols, rows=pty_size.rows),
            _request_timeout=http_timeout(request_timeout),
        )
