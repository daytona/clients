# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import AsyncIterator, Dict, List, Optional, Sequence, Union
from urllib.parse import quote

from typing_extensions import assert_never

from . import _clock, codes
from ._core import (
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    delete_process_spec,
    enum_str,
    expect_exit_result,
    expect_kill_processes_response,
    expect_log_frame_dto,
    expect_process_dto,
    logs_spec,
    parse_log_page,
    parse_process,
    parse_process_page,
    parse_sse_lines_async,
    parse_wait,
    process_create_spec,
    process_kill_spec,
    process_list_spec,
    resize_spec,
    signal_spec,
    stdin_spec,
    timeout_millis_from_seconds,
    wait_spec,
)
from ._transport import AsyncTransport
from .errors import ClientTimeoutError, ConnectionError, DaytonaError, UnsupportedOperationError, create_error
from .session import AsyncSession
from .types import (
    JsonValue,
    KeepLogs,
    ListResult,
    LogEncoding,
    LogFrame,
    LogPage,
    ProcessHandleRef,
    ProcessInfo,
    ProcessInfoKind,
    ProcessKind,
    ProcessResult,
    ProcessStateFilter,
    SignalName,
    StdinMode,
    TerminalSize,
    WaitResult,
    _is_process_info_kind_known,
)

INTERRUPT_GRACE_MS = 10_000
INTERRUPT_GRACE_SECONDS = INTERRUPT_GRACE_MS / 1000
INTERRUPT_RETRY_SECONDS = 0.25
WAIT_TRANSPORT_RETRY_LIMIT = 3

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


def _started_process_kind(reported: ProcessInfoKind, requested: ProcessKind) -> ProcessKind:
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
        if not page.next_cursor or page.next_cursor == cursor or not page.items:
            return frames
        cursor = page.next_cursor


class AsyncProcessHandle:
    """Serializable handle for one sandbox process."""

    def __init__(
        self,
        sandbox_id: str,
        process_id: str,
        transport: Optional[AsyncTransport] = None,
        kind: Optional[ProcessKind] = None,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.process_id = process_id
        self.id = process_id
        self._transport = transport
        self.kind = kind

    def to_json(self) -> Dict[str, str]:
        """Return a JSON-safe handle identity."""
        payload = {"sandboxId": self.sandbox_id, "processId": self.process_id}
        if self.kind is not None:
            payload["kind"] = self.kind
        return payload

    @classmethod
    def from_json(cls, value: Mapping[str, str]) -> AsyncProcessHandle:
        """Rehydrate a handle identity without making a network request."""
        return cls(value["sandboxId"], value["processId"], kind=_process_kind(value.get("kind")))

    def ref(self) -> ProcessHandleRef:
        """Return a typed handle identity."""
        return ProcessHandleRef(self.sandbox_id, self.process_id, self.kind)

    def bind(self, transport: AsyncTransport) -> AsyncProcessHandle:
        """Return this handle bound to a transport."""
        return AsyncProcessHandle(self.sandbox_id, self.process_id, transport, self.kind)

    async def get(self) -> ProcessInfo:
        """Fetch current process metadata."""
        return parse_process(
            expect_process_dto(
                await self._send(RequestSpec("GET", f"/processes/{quote(self.process_id, safe='')}"))
            )
        )

    async def wait(self, *, timeout: Optional[float] = None) -> WaitResult:
        """Wait for terminal state using the process long-poll contract.

        ``timeout`` is the caller wait budget in seconds; ``0`` waits
        unbounded.
        """
        if timeout is not None:
            timeout_millis_from_seconds(timeout)
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
                                self.process_id,
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
                            f"/processes/{quote(self.process_id, safe='')}",
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
            await self._send(
                logs_spec(self.process_id, cursor=cursor, limit=limit, encoding=encoding, follow=False)
            )
        )

    async def stream_logs(
        self,
        *,
        cursor: Optional[str] = None,
        encoding: LogEncoding = "text",
    ) -> AsyncIterator[LogFrame]:
        """Follow process logs with SSE while retaining cursor replay semantics."""
        transport = self._require_transport()
        async for event, payload in parse_sse_lines_async(
            transport.stream_lines(
                logs_spec(self.process_id, cursor=cursor, limit=None, encoding=encoding, follow=True)
            )
        ):
            if event == "log":
                yield parse_log_page([expect_log_frame_dto(payload)]).items[0]
            if event == "error":
                code = payload.get("code")
                raise create_error(
                    str(payload.get("message") or "SSE error"),
                    None,
                    str(code) if isinstance(code, str) else None,
                    "DAYTONA_DAEMON",
                )

    async def write_stdin(self, data: Union[str, bytes]) -> None:
        """Write to the stdin of an exec or PTY process created with stdin='open'."""
        self._require_stdin_kind("write_stdin")
        await self._send(stdin_spec(self.process_id, data, eof=False))

    async def close_stdin(self) -> None:
        """Close the stdin of an exec or PTY process (EOF)."""
        self._require_stdin_kind("close_stdin")
        await self._send(stdin_spec(self.process_id, "", eof=True))

    async def kill(
        self,
        *,
        signal: SignalName = "TERM",
        escalate_after: Optional[float] = None,
        escalate_to: SignalName = "KILL",
    ) -> None:
        """Send a signal and optional escalation signal."""
        await self._send(
            signal_spec(
                self.process_id,
                signal=signal,
                escalate_after_ms=timeout_millis_from_seconds(escalate_after),
                escalate_to=escalate_to,
            )
        )

    async def resize(self, cols: int, rows: int) -> None:
        """Resize a PTY process."""
        self._require_pty_kind("resize")
        await self._send(resize_spec(self.process_id, cols=cols, rows=rows))

    async def delete(self) -> None:
        """Delete the process record and retained logs, stopping it first.

        Raises ``UnsupportedOperationError`` for a running code cell and for a
        session command that has not stopped within 5 s: delete its session.
        """
        await self._send(delete_process_spec(self.process_id))

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
        sandbox_id: str,
        transport: AsyncTransport,
        language: Optional[str] = None,
        *,
        _session_id: Optional[str] = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._transport = transport
        self._language = language
        self._session_id = _session_id

    async def create_session(
        self,
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        shell: Optional[str] = None,
        login: Optional[bool] = None,
        name: Optional[str] = None,
        tag: Optional[str] = None,
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
            tag=tag,
        )
        return AsyncSession(self._sandbox_id, handle.id, self._transport)

    def connect_session(self, session_id: str | Mapping[str, object]) -> AsyncSession:
        """Bind a session id or serialized identity without making a request."""
        match session_id:
            case str():
                return AsyncSession(self._sandbox_id, session_id, self._transport)
            case Mapping():
                identity = AsyncSession.from_json(session_id)
                if identity.sandbox_id != self._sandbox_id:
                    raise create_error("session belongs to a different sandbox", 400, "BAD_REQUEST", "SDK")
                return AsyncSession(self._sandbox_id, identity.id, self._transport)
            case unreachable:
                assert_never(unreachable)

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
        terminal: Optional[TerminalSize] = None,
        keep_logs: Optional[KeepLogs] = None,
        tag: Optional[str] = None,
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
            terminal=terminal,
            keep_logs=keep_logs,
            tag=tag,
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
        tag: Optional[str] = None,
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
            tag=tag,
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
        terminal: Optional[TerminalSize] = None,
        keep_logs: Optional[KeepLogs] = None,
        tag: Optional[str] = None,
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
        :returns: :class:`~daytona.v2.types.ProcessResult` with ``stdout``, ``stderr``,
            ``output``, ``exit_code``, and ``reason``.

        Example::

            result = await sandbox.process.run("pip install httpx")
            if result.exit_code != 0:
                raise RuntimeError(result.stderr)
        """
        spec = process_create_spec(
            command,
            argv=argv,
            code=None,
            language=None,
            kind="exec",
            name=name,
            tag=tag,
            session_id=self._session_id,
            cwd=cwd,
            env=env,
            user=user,
            shell=shell,
            login=login,
            stdin=stdin,
            execution_timeout=execution_timeout,
            terminal=terminal,
            keep_logs="on_exit_ttl" if keep_logs in (None, "none") else keep_logs,
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
            handle.id,
            wait.exit_code,
            wait.signal,
            wait.reason,
            stdout,
            stderr,
            output,
            raw_reason=wait.raw_reason,
        )

    def connect(self, process_id: Union[str, Mapping[str, str]]) -> AsyncProcessHandle:
        """Create a bound process handle without fetching metadata."""
        if isinstance(process_id, Mapping):
            if process_id["sandboxId"] != self._sandbox_id:
                raise create_error("process handle belongs to a different sandbox", 400, "BAD_REQUEST", "SDK")
            return AsyncProcessHandle(
                self._sandbox_id,
                process_id["processId"],
                self._transport,
                _process_kind(process_id.get("kind")),
            )
        return AsyncProcessHandle(self._sandbox_id, process_id, self._transport)

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
        tag: Optional[str] = None,
    ) -> ListResult[ProcessInfo]:
        page = parse_process_page(
            await self._transport.send(
                process_list_spec(state=state, kind=kind, session_id=session_id, name=name, pid=pid, tag=tag)
            )
        )
        if tag is not None:
            # A sandbox whose daemon predates tags ignores the filter and
            # returns every process: keep only the tagged ones.
            page = dataclasses.replace(page, items=[item for item in page.items if item.tag == tag])
        return page


    async def kill_all(self, *, tag: str, timeout: Optional[float] = None) -> List[str]:
        """Kill every process with the tag and everything those processes started.

        Returns their process ids once nothing started under the tag is running,
        including detached descendants and survivors of exited processes. The tag
        is reusable after this call. Records and logs are kept. Survivors of
        deleted or expired records are killed too but not listed.

        ``timeout`` is in seconds (up to 60, fractions allowed, default 10).
        Raises ``ProcessKillIncompleteError`` on timeout; retrying is safe.
        Raises ``UnsupportedOperationError`` on macOS and sandboxes without
        process containment.
        """
        return expect_kill_processes_response(
            await self._transport.send(process_kill_spec(tag=tag, timeout=timeout))
        ).process_ids


class AsyncPtyService:
    """PTY convenience namespace."""

    def __init__(self, process: AsyncProcessService) -> None:
        self._process = process

    async def start(
        self,
        command: Optional[str] = None,
        *,
        cols: int = 80,
        rows: int = 24,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        name: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> AsyncProcessHandle:
        """Start a detached PTY process."""
        return await self._process._start_process(
            command,
            kind="pty",
            cwd=cwd,
            env=env,
            name=name,
            tag=tag,
            stdin="open",
            terminal=TerminalSize(cols=cols, rows=rows),
            keep_logs="until_cleanup",
        )

    def connect(self, process_id: str) -> AsyncProcessHandle:
        """Create a bound PTY handle."""
        return self._process.connect(process_id)

    async def list(self) -> ListResult[ProcessInfo]:
        return await self._process.list(kind="pty")

    async def delete(self, process_id: str) -> None:
        handle = self.connect(process_id)
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
