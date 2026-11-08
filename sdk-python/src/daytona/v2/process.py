# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Mapping
from typing import AsyncIterator, List, Optional, Sequence, Union
from urllib.parse import quote

from . import _clock
from . import _codes as codes
from ._core import (
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    delete_process_spec,
    enum_str,
    expect_exit_result,
    expect_process_dto,
    logs_spec,
    parse_log_frame,
    parse_log_page,
    parse_process,
    parse_process_page,
    parse_sse_messages_async,
    parse_wait,
    process_create_spec,
    process_list_spec,
    resize_spec,
    signal_spec,
    sse_json,
    stdin_spec,
    timeout_millis_from_seconds,
    wait_budget,
    wait_spec,
)
from ._transport import AsyncTransport
from .errors import ClientTimeoutError, ConnectionError, DaytonaError, UnsupportedOperationError, create_error
from .session import AsyncSession
from .session import _require_id as _require_session_id
from .types import (
    JsonValue,
    KeepLogs,
    ListResult,
    LogEncoding,
    LogFrame,
    LogPage,
    ProcessHandleJSON,
    ProcessInfo,
    ProcessKind,
    ProcessReportedKind,
    ProcessResult,
    ProcessSignal,
    ProcessStateFilter,
    RunKeepLogs,
    SessionJSON,
    StdinMode,
    StreamLogEofEvent,
    StreamLogEvent,
    StreamLogFrameEvent,
    StreamLogStateEvent,
    StreamLogWarningEvent,
    TerminalSize,
    WaitResult,
    _is_process_info_kind_known,
)

INTERRUPT_GRACE_MS = 10_000
INTERRUPT_GRACE_SECONDS = INTERRUPT_GRACE_MS / 1000
INTERRUPT_RETRY_SECONDS = 0.25
WAIT_TRANSPORT_RETRY_LIMIT = 3

DEFAULT_TERMINAL_SIZE = TerminalSize(cols=80, rows=24)


def _run_keep_logs(keep_logs: Optional[RunKeepLogs]) -> RunKeepLogs:
    if keep_logs is None:
        return "on_exit_ttl"
    if keep_logs not in ("on_exit_ttl", "until_cleanup"):
        raise create_error(
            f"keep_logs={keep_logs!r} is not supported by run(); use 'on_exit_ttl' or 'until_cleanup' "
            "(start() accepts 'none')",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return keep_logs


# Wait budget for run() when the caller set no process timeout: the server's
# long-poll cap, after which run() re-issues the wait.
RUN_WAIT_SECONDS = 60.0


def _remaining_timeout(timeout: Optional[float], started: float) -> Optional[float]:
    if timeout is None:
        return None
    if timeout == 0:
        return None
    return max(0.0, timeout - (_clock.monotonic() - started))


def _process_kind(value: Optional[str]) -> Optional[ProcessKind]:
    if value is not None and _is_process_info_kind_known(value):
        return value
    return None


def _started_process_kind(reported: ProcessReportedKind, requested: ProcessKind) -> ProcessKind:
    """The kind a new handle carries. The API reports ``kind`` from an open
    vocabulary and the SDK normalizes an unlisted one to ``"unknown"``; a
    handle's kind gates the pty-only verbs and is serialized, so an unlisted
    kind falls back to the kind the request asked for (TS: startedProcessKind)."""
    if _is_process_info_kind_known(reported):
        return reported
    return requested


# Pages of logs fetched for a process that is STILL RUNNING when the caller
# budget expires. A live process can out-write the paginator, so the snapshot
# returned with a ``timed_out`` result is bounded instead of chasing the tail.
# Same value as the TS SDK's TIMED_OUT_LOG_PAGE_LIMIT.
TIMED_OUT_LOG_PAGE_LIMIT = 8


async def _all_logs(handle: AsyncProcessHandle, *, max_pages: Optional[int] = None) -> List[LogFrame]:
    frames: List[LogFrame] = []
    cursor: Optional[str] = None
    pages = 0
    while True:
        if max_pages is not None and pages >= max_pages:
            return frames
        page = await handle.logs(cursor=cursor)
        pages += 1
        frames.extend(page.items)
        if page.eof or not page.next_cursor or page.next_cursor == cursor or not page.items:
            return frames
        cursor = page.next_cursor


def _malformed_event(event: str, field: str) -> DaytonaError:
    return create_error(f"expected process log {event} event: {field} is required", 400, "BAD_REQUEST", "SDK")


def _stream_log_event(event: str, data: str, message_id: Optional[str]) -> Optional[StreamLogEvent]:
    """One SSE event of a followed process log; ``None`` for an event this release ignores.

    An event's cursor is its payload's, else the SSE ``id:`` line's; an event
    missing a field the contract requires raises the malformed-response error.
    """
    if event not in ("log", "state", "warning", "eof", "error"):
        return None
    payload = sse_json(event, data, f"process log {event} event")
    cursor_value = payload.get("cursor")
    cursor = cursor_value if isinstance(cursor_value, str) and cursor_value else message_id
    if not cursor:
        raise _malformed_event(event, "cursor")
    if event == "log":
        frame = parse_log_frame({**payload, "cursor": cursor})
        return StreamLogFrameEvent(frame=frame, cursor=cursor)
    if event == "state":
        return StreamLogStateEvent(result=parse_wait(expect_exit_result(payload)), cursor=cursor)
    if event == "eof":
        return StreamLogEofEvent(cursor=cursor)
    message = payload.get("message")
    if not isinstance(message, str):
        raise _malformed_event(event, "message")
    if event == "warning":
        first_available = payload.get("firstAvailableCursor")
        if not isinstance(first_available, str):
            raise _malformed_event(event, "firstAvailableCursor")
        return StreamLogWarningEvent(message=message, cursor=cursor, first_available_cursor=first_available)
    code = payload.get("code")
    source = payload.get("source")
    raise create_error(
        message,
        None,
        code if isinstance(code, str) else None,
        source if isinstance(source, str) else "DAYTONA_DAEMON",
    )


def _required_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise create_error(
            f"process handle identity needs a non-empty string {field}", 400, "BAD_REQUEST", "SDK"
        )
    return value


class AsyncProcessHandle:
    """Handle for one sandbox process.

    Returned by ``process.start()`` / ``process.connect()`` and the PTY and
    session namespaces, never constructed directly. :meth:`to_json` gives a
    JSON-safe identity that ``sandbox.process.connect()`` accepts on any worker.
    """

    def __init__(
        self,
        _sandbox_id: str,
        _process_id: str,
        _transport: Optional[AsyncTransport] = None,
        _kind: Optional[ProcessKind] = None,
    ) -> None:
        self.sandbox_id: str = _sandbox_id
        self.id: str = _process_id
        self.kind: Optional[ProcessKind] = _kind
        self._transport = _transport

    def __repr__(self) -> str:
        return f"AsyncProcessHandle(sandbox_id={self.sandbox_id!r}, id={self.id!r}, kind={self.kind!r})"

    def to_json(self) -> ProcessHandleJSON:
        """Return the handle's JSON-safe identity for ``process.connect()``."""
        payload: ProcessHandleJSON = {"sandboxId": self.sandbox_id, "processId": self.id}
        if self.kind is not None:
            payload["kind"] = self.kind
        return payload

    async def get(self) -> ProcessInfo:
        """Fetch current process metadata."""
        return parse_process(
            expect_process_dto(await self._send(RequestSpec("GET", f"/processes/{quote(self.id, safe='')}")))
        )

    async def wait(self, *, timeout: Optional[float] = None) -> WaitResult:
        """Wait for terminal state using the process long-poll contract.

        ``timeout`` is the caller wait budget in seconds; ``0`` waits
        unbounded.
        """
        timeout = wait_budget(timeout)
        deadline = None if timeout is None or timeout == 0 else _clock.monotonic() + timeout
        consecutive_transport_failures = 0
        while True:
            if deadline is None:
                remaining_timeout = None
            else:
                remaining_timeout = deadline - _clock.monotonic()
                if remaining_timeout <= 0:
                    return WaitResult(reason="timed_out")
            # One retried unit: the long-poll plus, on `timed_out`, the
            # record read; the failure streak resets only when both succeed.
            try:
                result = parse_wait(
                    expect_exit_result(
                        await self._send(
                            wait_spec(
                                self.id,
                                timeout=remaining_timeout,
                                unbounded=deadline is None,
                            )
                        )
                    )
                )
                if result.reason != "timed_out":
                    return result
                if deadline is not None and _clock.monotonic() >= deadline:
                    return WaitResult(reason="timed_out")
                # Budget left: `timed_out` is either an expired long-poll or
                # a command its own deadline stopped, and the record says which.
                record = expect_process_dto(
                    await self._send(
                        RequestSpec(
                            "GET",
                            f"/processes/{quote(self.id, safe='')}",
                            timeout=None
                            if deadline is None
                            else deadline - _clock.monotonic() + WAIT_READ_SLACK_SECONDS,
                        )
                    )
                )
            except (ConnectionError, ClientTimeoutError):
                consecutive_transport_failures += 1
                if deadline is not None and _clock.monotonic() >= deadline:
                    return WaitResult(reason="timed_out")
                if consecutive_transport_failures > WAIT_TRANSPORT_RETRY_LIMIT:
                    raise
                continue
            consecutive_transport_failures = 0
            if enum_str(record.state) == "terminal":
                return parse_wait(record)

    async def logs(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        encoding: LogEncoding = "text",
    ) -> LogPage:
        """Read a cursor-replayable process log page."""
        return parse_log_page(
            await self._send(logs_spec(self.id, cursor=cursor, limit=limit, encoding=encoding, follow=False))
        )

    async def stream_logs(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        encoding: LogEncoding = "text",
    ) -> AsyncIterator[StreamLogEvent]:
        """Follow the process log as it is written, replaying from ``cursor``.

        Yields one :data:`StreamLogEvent` per server event: ``log`` (a frame),
        ``state`` (the process became terminal), ``warning`` (frames before
        ``first_available_cursor`` were evicted) and ``eof``. A stream error
        raises the typed error for its code.
        """
        transport = self._require_transport()
        async for event, data, message_id in parse_sse_messages_async(
            transport.stream_lines(
                logs_spec(self.id, cursor=cursor, limit=limit, encoding=encoding, follow=True)
            )
        ):
            parsed = _stream_log_event(event, data, message_id)
            if parsed is not None:
                yield parsed

    async def write_stdin(self, data: Union[str, bytes]) -> None:
        """Write to the stdin of an exec or PTY process created with stdin='open'."""
        self._require_stdin_kind("write_stdin")
        await self._send(stdin_spec(self.id, data, eof=False))

    async def close_stdin(self) -> None:
        """Close the stdin of an exec or PTY process (EOF)."""
        self._require_stdin_kind("close_stdin")
        await self._send(stdin_spec(self.id, "", eof=True))

    async def kill(
        self,
        *,
        signal: ProcessSignal = "TERM",
        escalate_after: Optional[float] = None,
        escalate_to: ProcessSignal = "KILL",
    ) -> None:
        """Send a signal and optional escalation signal."""
        await self._send(
            signal_spec(
                self.id,
                signal=signal,
                escalate_after_ms=timeout_millis_from_seconds(escalate_after),
                escalate_to=escalate_to,
            )
        )

    async def resize(self, cols: int, rows: int) -> None:
        """Resize a PTY process."""
        self._require_pty_kind("resize")
        await self._send(resize_spec(self.id, cols=cols, rows=rows))

    async def delete(self) -> None:
        """Delete the process record and retained logs, stopping it first.

        Raises ``UnsupportedOperationError`` for a running code cell and for a
        session command that has not stopped within 5 s: delete its session.
        """
        await self._send(delete_process_spec(self.id))

    async def _send(self, spec: RequestSpec) -> JsonValue | bytes:
        return await self._require_transport().send(spec)

    def _require_transport(self) -> AsyncTransport:
        if self._transport is None:
            raise create_error(
                "process handle is not bound to a sandbox transport", 400, "BAD_REQUEST", "SDK"
            )
        return self._transport

    def _require_pty_kind(self, verb: str) -> None:
        if self.kind is not None and self.kind != "pty":
            raise UnsupportedOperationError(
                f"process.{verb}() is only supported for pty handles; handle kind is {self.kind}",
                status_code=400,
                code=codes.UNSUPPORTED_OPERATION,
                source="SDK",
            )

    def _require_stdin_kind(self, verb: str) -> None:
        if self.kind in ("code", "session"):
            raise UnsupportedOperationError(
                f"process.{verb}() is only supported for exec and pty handles; handle kind is {self.kind}",
                status_code=400,
                code=codes.UNSUPPORTED_OPERATION,
                source="SDK",
            )


async def wait_or_interrupt(
    handle: AsyncProcessHandle, *, timeout: Optional[float], expired: bool = False
) -> WaitResult:
    """Wait for a code execution within the caller's deadline.

    ``timeout`` is the caller wait budget in seconds (``None``/``0`` = unbounded).
    On expiry, the execution is interrupted and the interrupted result is
    returned. Without a deadline, waits until terminal across long-poll
    rounds.
    """
    # ``timeout`` follows ProcessHandle.wait(): ``None``/``0`` = unbounded. A
    # caller whose positive budget was already spent (e.g. on process
    # start-up) says so with ``expired=True`` — a computed zero must never be
    # confused with an explicit unbounded ``0``.
    wait = WaitResult(reason="timed_out") if expired else await handle.wait(timeout=timeout)
    if wait.reason != "timed_out" or wait.exit_code is not None:
        return wait
    if wait.reason == "timed_out" and wait.exit_code is None:
        interrupt_deadline = _clock.monotonic() + INTERRUPT_GRACE_SECONDS
        interrupted = False
        while not interrupted and _clock.monotonic() < interrupt_deadline:
            try:
                await handle.kill(signal="INT")
                interrupted = True
            except DaytonaError as exc:
                if exc.code == codes.PROCESS_TERMINAL:
                    return await handle.wait(timeout=INTERRUPT_GRACE_SECONDS)
                if exc.code != codes.UNSUPPORTED_OPERATION:
                    raise
                wait = await handle.wait(timeout=INTERRUPT_RETRY_SECONDS)
                if wait.reason != "timed_out":
                    return wait
        if not interrupted:
            return wait
        wait = await handle.wait(timeout=INTERRUPT_GRACE_SECONDS)
    return wait


class AsyncProcessService:
    """Sandbox process namespace."""

    def __init__(
        self,
        _sandbox_id: str,
        _transport: AsyncTransport,
        _language: Optional[str] = None,
        *,
        _session_id: Optional[str] = None,
    ) -> None:
        self._sandbox_id = _sandbox_id
        self._transport = _transport
        self._language = _language
        self._session_id = _session_id

    async def create_session(
        self,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        name: Optional[str] = None,
    ) -> AsyncSession:
        """Create one persistent shell; cwd and env apply to all its commands.

        ``shell`` must be a POSIX shell (bash, sh, dash, zsh, ksh) on Linux
        and macOS; another shell is refused when the session is created.
        """
        handle = await self._start_process(
            kind="session",
            cwd=cwd,
            env=env,
            shell=shell,
            login=login,
            name=name,
        )
        return AsyncSession(self._sandbox_id, handle.id, self._transport)

    def connect_session(self, session: str | SessionJSON) -> AsyncSession:
        """Reconnect a shell session by id or by ``session.to_json()``, without a request."""
        if isinstance(session, Mapping):
            identity = AsyncSession._from_json(session)
            if identity.sandbox_id != self._sandbox_id:
                raise create_error("session belongs to a different sandbox", 400, "BAD_REQUEST", "SDK")
            return AsyncSession(self._sandbox_id, identity.id, self._transport)
        return AsyncSession(self._sandbox_id, _require_session_id(session, "sessionId"), self._transport)

    async def start(
        self,
        command: Optional[str] = None,
        *,
        argv: Optional[Sequence[str]] = None,
        name: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        user: Optional[str] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        stdin: StdinMode = "closed",
        execution_timeout: Optional[float] = None,
        keep_logs: Optional[KeepLogs] = None,
    ) -> AsyncProcessHandle:
        """Start a detached process and return a handle immediately.

        Unlike :meth:`run`, this returns as soon as the sandbox has started the
        process. Use the returned :class:`ProcessHandle` to stream logs
        (``handle.stream_logs()``), wait for exit (``handle.wait()``), or kill
        (``handle.kill()``). This is the right choice for long-running servers —
        call ``start()`` once, then poll the port or stream logs to confirm the
        server is ready.

        **Shell selection.** A string ``command`` runs through the sandbox shell
        (``bash``, else ``sh``; pin another with ``shell=``). Quote globs and brackets
        (``pip install -e '.[test]'``). Use ``argv=`` to bypass the shell for
        untrusted input. For persistent shell state, use :meth:`create_session`.

        :param command: Shell command string; mutually exclusive with ``argv``.
        :param argv: Argument vector for shell-free execution.
        :param cwd: Working directory inside the sandbox.
        :param env: Extra environment variables merged into the process environment.
        :param shell: Shell binary to use (e.g. ``"bash"``); overrides auto-detection.
        :param execution_timeout: Server-side deadline in seconds. The sandbox kills
            the process tree when this expires.
        :returns: Bound :class:`ProcessHandle` — logs, wait, and kill are available
            immediately.

        Example — start an HTTP server and wait for it to be ready::

            handle = await sandbox.process.start("python3 -m http.server 8000")
            # poll or stream logs; the process keeps running in the background
        """
        return await self._start_process(
            command,
            argv=argv,
            name=name,
            session_id=self._session_id,
            cwd=cwd,
            env=env,
            user=user,
            shell=shell,
            login=login,
            stdin=stdin,
            execution_timeout=execution_timeout,
            keep_logs=keep_logs,
        )

    async def _start_process(
        self,
        command: Optional[str] = None,
        *,
        argv: Optional[Sequence[str]] = None,
        code: Optional[str] = None,
        language: Optional[str] = None,
        kind: Optional[ProcessKind] = None,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        user: Optional[str] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        stdin: StdinMode = "closed",
        execution_timeout: Optional[float] = None,
        terminal: Optional[TerminalSize] = None,
        keep_logs: Optional[KeepLogs] = None,
        request_timeout: Optional[float] = None,
    ) -> AsyncProcessHandle:
        requested_kind: ProcessKind
        if kind is not None:
            requested_kind = kind
        else:
            requested_kind = "code" if code is not None else "exec"
        spec = process_create_spec(
            command,
            argv=argv,
            code=code,
            language=None if requested_kind == "session" else language or self._language,
            kind=requested_kind,
            name=name,
            session_id=session_id,
            cwd=cwd,
            env=env,
            user=user,
            shell=shell,
            login=login,
            stdin=stdin,
            execution_timeout=execution_timeout,
            terminal=terminal,
            keep_logs=keep_logs,
            request_timeout=request_timeout,
        )
        info = parse_process(expect_process_dto(await self._transport.send(spec)))
        return AsyncProcessHandle(
            self._sandbox_id, info.id, self._transport, _started_process_kind(info.kind, requested_kind)
        )

    async def run(
        self,
        command: Optional[str] = None,
        *,
        argv: Optional[Sequence[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        user: Optional[str] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        stdin: StdinMode = "closed",
        timeout: Optional[float] = None,
        execution_timeout: Optional[float] = None,
        name: Optional[str] = None,
        keep_logs: Optional[RunKeepLogs] = None,
    ) -> ProcessResult:
        """Start a process, wait for its shell to exit, and collect its output.

        The common case is one round trip: the sandbox starts the process, holds
        the request until it exits (or ``timeout`` expires), and returns the
        output inline. A process that outlives the wait window or produces more
        output than fits in one page falls back to split calls automatically.

        The result holds the output available when the call returns:
        its per-process log is bounded, so very large output loses its oldest
        frames, and a result that timed out collects only the first few log
        pages.

        **Shell selection.** A string ``command`` runs through the sandbox shell
        (``bash``, else ``sh``; pin another with ``shell=``). Quote globs and brackets
        (``pip install -e '.[test]'``) so the command means the same under any
        shell. Use ``argv=`` to bypass the shell entirely, which is the safe
        form for untrusted arguments. For persistent shell state, use :meth:`create_session`.

        **Background processes.** The call returns when the shell exits. If the
        command forks a background child that holds its output descriptors open
        (e.g. ``python3 -m http.server 8000 &``), the call waits at most ~2
        seconds for output to drain and then returns; output the child writes
        later is not part of the result. For a long-running server prefer
        :meth:`start`, which returns a :class:`ProcessHandle` immediately, or
        redirect: ``nohup cmd > server.log 2>&1 &``.

        :param command: Shell command string; mutually exclusive with ``argv``.
        :param argv: Argument vector for shell-free execution.
        :param cwd: Working directory inside the sandbox.
        :param env: Extra environment variables merged into the process environment.
        :param shell: Shell binary to use (e.g. ``"bash"``); overrides auto-detection.
        :param timeout: Caller wait budget in seconds. Omit to wait until the process
            ends. Does not kill the process on expiry.
        :param execution_timeout: Server-side deadline in seconds. The sandbox kills
            the process tree when this expires.
        :param keep_logs: How long the sandbox keeps the process log: ``"on_exit_ttl"``
            (default) or ``"until_cleanup"``. ``"none"`` is not accepted: the output is
            read back from the log; use :meth:`start` for a process without one.
        :returns: :class:`~daytona.v2.types.ProcessResult` with ``stdout``, ``stderr``,
            ``output``, ``logs``, ``exit_code``, and ``reason``.

        Example::

            result = await sandbox.process.run("pip install httpx")
            if result.exit_code != 0:
                raise RuntimeError(result.stderr)
        """
        timeout = wait_budget(timeout)
        spec = process_create_spec(
            command,
            argv=argv,
            code=None,
            language=None,
            kind="exec",
            name=name,
            session_id=self._session_id,
            cwd=cwd,
            env=env,
            user=user,
            shell=shell,
            login=login,
            stdin=stdin,
            execution_timeout=execution_timeout,
            terminal=None,
            keep_logs=_run_keep_logs(keep_logs),
            wait_ms=timeout_millis_from_seconds(timeout)
            if timeout is not None
            else int(RUN_WAIT_SECONDS * 1000),
            inline_logs=True,
        )
        started = _clock.monotonic()
        payload = expect_process_dto(await self._transport.send(spec))
        info = parse_process(payload)
        handle = AsyncProcessHandle(
            self._sandbox_id, info.id, self._transport, _started_process_kind(info.kind, "exec")
        )
        inline = payload.logs
        settled = payload.state.value == "terminal"
        remaining_timeout = _remaining_timeout(timeout, started)
        if settled:
            wait = parse_wait(payload)
        elif remaining_timeout is not None and remaining_timeout <= 0:
            wait = WaitResult(reason="timed_out")
        else:
            wait = await handle.wait(timeout=remaining_timeout)
        if settled and inline is not None and inline.eof:
            logs = parse_log_page(inline).items
        else:
            logs = await _all_logs(
                handle, max_pages=TIMED_OUT_LOG_PAGE_LIMIT if wait.reason == "timed_out" else None
            )
        stdout = "".join(frame.data for frame in logs if frame.channel in ("stdout", "pty"))
        stderr = "".join(frame.data for frame in logs if frame.channel == "stderr")
        output = "".join(frame.data for frame in logs if frame.channel in ("stdout", "stderr", "pty"))
        return ProcessResult(
            process_id=handle.id,
            reason=wait.reason,
            exit_code=wait.exit_code,
            signal=wait.signal,
            raw_reason=wait.raw_reason,
            stdout=stdout,
            stderr=stderr,
            output=output,
            logs=logs,
        )

    def connect(self, process: Union[str, ProcessHandleJSON]) -> AsyncProcessHandle:
        """Reconnect a process by id or by ``handle.to_json()``, without a request."""
        if isinstance(process, Mapping):
            if _required_id(process.get("sandboxId"), "sandboxId") != self._sandbox_id:
                raise create_error("process handle belongs to a different sandbox", 400, "BAD_REQUEST", "SDK")
            return AsyncProcessHandle(
                self._sandbox_id,
                _required_id(process.get("processId"), "processId"),
                self._transport,
                _process_kind(process.get("kind")),
            )
        return AsyncProcessHandle(self._sandbox_id, _required_id(process, "processId"), self._transport)

    async def get(self, process_id: str) -> ProcessInfo:
        """Fetch current process metadata by id or live name alias."""
        return parse_process(
            expect_process_dto(
                await self._transport.send(RequestSpec("GET", f"/processes/{quote(process_id, safe='')}"))
            )
        )

    async def list(
        self,
        *,
        state: Optional[ProcessStateFilter] = None,
        kind: Optional[ProcessKind] = None,
        session_id: Optional[str] = None,
        name: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> ListResult[ProcessInfo]:
        return parse_process_page(
            await self._transport.send(
                process_list_spec(state=state, kind=kind, session_id=session_id, name=name, pid=pid)
            )
        )


class AsyncPtyService:
    """PTY namespace: interactive terminals."""

    def __init__(self, _process: AsyncProcessService) -> None:
        self._process = _process

    async def start(
        self,
        command: Optional[str] = None,
        *,
        terminal: Optional[TerminalSize] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        name: Optional[str] = None,
        user: Optional[str] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        stdin: StdinMode = "open",
        execution_timeout: Optional[float] = None,
        keep_logs: KeepLogs = "until_cleanup",
    ) -> AsyncProcessHandle:
        """Start a detached PTY process; without ``command`` it runs the sandbox shell.

        ``terminal`` defaults to 80 columns by 24 rows. ``execution_timeout`` is
        a server-side deadline in seconds.
        """
        return await self._process._start_process(
            command,
            kind="pty",
            cwd=cwd,
            env=env,
            name=name,
            user=user,
            shell=shell,
            login=login,
            stdin=stdin,
            execution_timeout=execution_timeout,
            terminal=terminal if terminal is not None else DEFAULT_TERMINAL_SIZE,
            keep_logs=keep_logs,
        )

    def connect(self, process: Union[str, ProcessHandleJSON]) -> AsyncProcessHandle:
        """Reconnect a PTY by id or by ``handle.to_json()``, without a request."""
        return self._process.connect(process)

    async def list(self) -> ListResult[ProcessInfo]:
        return await self._process.list(kind="pty")

    async def delete(self, process: Union[str, ProcessHandleJSON]) -> None:
        """Stop a PTY and delete it and its log."""
        handle = self.connect(process)
        # The pty namespace only owns PTYs: refuse to kill an exec/code process by id.
        info = await handle.get()
        if info.kind != "pty":
            raise UnsupportedOperationError(
                f"process.delete() is only supported for pty handles; handle kind is {info.kind}",
                status_code=400,
                code=codes.UNSUPPORTED_OPERATION,
                source="SDK",
            )
        try:
            await handle.kill()
        except DaytonaError as exc:
            if exc.code != "PROCESS_TERMINAL":
                raise
        await handle.delete()
