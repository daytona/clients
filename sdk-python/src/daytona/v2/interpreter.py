# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Awaitable, Callable, Mapping, Optional, TypeGuard
from urllib.parse import quote

from . import _clock
from . import _codes as codes
from ._core import (
    RequestSpec,
    bounded_request_timeout,
    expect_session_dto,
    expect_session_list_response,
    timeout_millis_from_seconds,
)
from ._transport import AsyncTransport
from .errors import DaytonaError, create_error
from .process import (
    TIMED_OUT_LOG_PAGE_LIMIT,
    AsyncProcessHandle,
    AsyncProcessService,
    _all_logs,
    wait_or_interrupt,
)
from .types import (
    AsyncInterpreterContext,
    InterpreterContextJSON,
    InterpreterError,
    InterpreterLogs,
    InterpreterOutput,
    InterpreterProcess,
    InterpreterResult,
    JsonObject,
    JsonValue,
)

SHELL_SESSION_KERNEL = "shell"


def _implicit_context_id(language: str) -> str:
    return f"code-{language}"


def _isolated_context_id(language: str) -> str:
    return f"isolated-{language}-{uuid.uuid4().hex}"


def _is_interpreter_context_json(value: object) -> TypeGuard[InterpreterContextJSON]:
    if not isinstance(value, Mapping):
        return False
    sandbox_id = value.get("sandbox_id")
    context_id = value.get("context_id")
    language = value.get("language")
    return isinstance(sandbox_id, str) and isinstance(context_id, str) and isinstance(language, str)


def _timed_out_result() -> InterpreterResult:
    return InterpreterResult(
        process=InterpreterProcess("", None, None, "timed_out", None),
        results=[],
        logs=InterpreterLogs(),
        error=None,
    )


def _remaining_budget(timeout: Optional[float], started: float) -> Optional[float]:
    """Caller budget left after ``started``.

    The caller's own unbounded spelling is preserved (``None`` stays ``None``,
    a literal ``0`` stays ``0``) so the next hop still sees the explicit
    "unbounded" marker rather than an ordinary omitted timeout.
    """
    if timeout is None or timeout == 0:
        return timeout
    return timeout - (_clock.monotonic() - started)


class _ContextExecutionGate:
    """Serialises runs against one interpreter context.

    Single-loop, so the dict of gates needs no lock; the condition variable
    is still needed because waiters interleave across ``await``.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._running = False
        self._waiting = 0
        self._closing = False
        self._on_idle: Callable[[], None] | None = None

    def request_eviction(self, on_idle: Callable[[], None]) -> bool:
        self._closing = True
        self._on_idle = on_idle
        return not self._running and self._waiting == 0

    async def run(
        self,
        timeout: Optional[float],
        task: Callable[[Optional[float]], Awaitable[InterpreterResult]],
    ) -> InterpreterResult:
        timeout_millis_from_seconds(timeout)
        if timeout is None or timeout == 0:
            deadline = None
        else:
            deadline = _clock.monotonic() + timeout
        async with self._condition:
            self._waiting += 1
            try:
                while self._running:
                    remaining_timeout = None if deadline is None else deadline - _clock.monotonic()
                    if remaining_timeout is not None and remaining_timeout <= 0:
                        return _timed_out_result()
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=remaining_timeout)
                    except asyncio.TimeoutError:
                        return _timed_out_result()
                self._running = True
            finally:
                self._waiting -= 1
        try:
            if deadline is None:
                # Preserve the caller's own unbounded spelling (None or a
                # literal 0) so the next hop still sees the explicit marker.
                remaining_timeout = timeout
            else:
                remaining_timeout = deadline - _clock.monotonic()
                if remaining_timeout <= 0:
                    return _timed_out_result()
            return await task(remaining_timeout)
        finally:
            on_idle = None
            async with self._condition:
                self._running = False
                self._condition.notify_all()
                if self._closing and self._waiting == 0:
                    on_idle = self._on_idle
            if on_idle is not None:
                on_idle()


class AsyncInterpreterService:
    def __init__(self, sandbox_id: str, transport: AsyncTransport, process: AsyncProcessService) -> None:
        self._sandbox_id = sandbox_id
        self._transport = transport
        self._process = process
        self._context_gates: dict[str, _ContextExecutionGate] = {}

    def _gate_for(self, context_id: str) -> _ContextExecutionGate:
        gate = self._context_gates.get(context_id)
        if gate is None:
            gate = _ContextExecutionGate()
            self._context_gates[context_id] = gate
        return gate

    def _discard_gate_if_same(self, context_id: str, gate: _ContextExecutionGate) -> None:
        if self._context_gates.get(context_id) is gate:
            self._context_gates.pop(context_id, None)

    def _evict_gate_when_drained(self, context_id: str) -> None:
        gate = self._context_gates.get(context_id)
        if gate is None:
            return
        should_discard = gate.request_eviction(lambda: self._discard_gate_if_same(context_id, gate))
        if should_discard:
            self._context_gates.pop(context_id, None)

    async def create_context(
        self,
        *,
        language: str,
        context_id: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        request_timeout: Optional[float] = None,
    ) -> AsyncInterpreterContext:
        body: JsonObject = {"kernel": language}
        if context_id is not None:
            body["id"] = context_id
        if cwd is not None:
            body["cwd"] = cwd
        if env is not None:
            body["env"] = dict(env)
        timeout_millis_from_seconds(request_timeout)
        payload = expect_session_dto(
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/sessions",
                    json_body=body,
                    timeout=bounded_request_timeout(request_timeout),
                    unbounded_read=request_timeout == 0,
                )
            )
        )
        resolved_context_id = payload.id or (context_id or "")
        return self._bind_context(resolved_context_id, language)

    def connect_context(
        self,
        context: str | AsyncInterpreterContext | InterpreterContextJSON,
        *,
        language: str | None = None,
    ) -> AsyncInterpreterContext:
        if _is_interpreter_context_json(context):
            return self._bind_context(*self._context_identity(context))
        if isinstance(context, AsyncInterpreterContext):
            return self._bind_context(*self._context_identity(context))
        if language is None:
            raise create_error(
                "language is required when reconnecting an interpreter context by id",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        return self._bind_context(context, language)

    def get_context(
        self,
        context: str | AsyncInterpreterContext | InterpreterContextJSON,
        *,
        language: str | None = None,
    ) -> AsyncInterpreterContext:
        return self.connect_context(context, language=language)

    async def list_contexts(self) -> list[AsyncInterpreterContext]:
        payload = expect_session_list_response(await self._transport.send(RequestSpec("GET", "/sessions")))
        contexts: list[AsyncInterpreterContext] = []
        for item in payload.items:
            kernel = item.kernel.value
            if kernel == SHELL_SESSION_KERNEL:
                continue
            contexts.append(self._bind_context(item.id, kernel))
        return contexts

    async def delete_context(self, context: AsyncInterpreterContext | InterpreterContextJSON | str) -> None:
        if _is_interpreter_context_json(context) or isinstance(context, AsyncInterpreterContext):
            context_id, _ = self._context_identity(context)
        else:
            context_id = context
        try:
            await self._transport.send(RequestSpec("DELETE", f"/sessions/{quote(context_id, safe='')}"))
        except DaytonaError as error:
            if error.code == codes.SESSION_NOT_FOUND:
                self._evict_gate_when_drained(context_id)
            raise
        self._evict_gate_when_drained(context_id)

    async def run(
        self,
        code: str,
        *,
        language: str = "python",
        context: Optional[AsyncInterpreterContext] = None,
        isolated: bool = False,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> InterpreterResult:
        """Execute code and return the parsed interpreter result.

        A rehydrated ``kind='code'`` process handle can still replay logs and
        wait/kill, but it does not reconstruct this decomposed result on
        another worker. A vanished explicit context surfaces the API's typed
        ``SESSION_NOT_FOUND`` error; callers decide whether to recreate the
        context and what to rerun. ``timeout`` is the caller wait budget in
        seconds; ``0`` waits unbounded.
        """
        if isolated and context is not None:
            raise create_error(
                "isolated runs cannot also target an explicit interpreter context",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        if isolated:
            # Reject an invalid budget before any request is issued, like the
            # other run paths do.
            timeout_millis_from_seconds(timeout)
            # The throwaway session's POST /sessions is part of this run and
            # spends the same caller budget as the execution itself.
            started = _clock.monotonic()
            context_id = _isolated_context_id(language)
            try:
                isolated_context = await self.create_context(
                    language=language,
                    context_id=context_id,
                    cwd=cwd,
                    env=env,
                    request_timeout=timeout,
                )
                remaining = _remaining_budget(timeout, started)
                # A positive budget that setup consumed expires here; the
                # caller's own unbounded 0 passes through untouched.
                if timeout and remaining is not None and remaining <= 0:
                    raise create_error(
                        "interpreter run timed out while creating its isolated context",
                        408,
                        "REQUEST_TIMEOUT",
                        "SDK",
                    )
                return await isolated_context.run(code, cwd=cwd, timeout=remaining)
            finally:
                await self._delete_context_best_effort(context_id)
        if context is None:
            gate = self._gate_for(_implicit_context_id(language))
            return await gate.run(
                timeout,
                lambda remaining_timeout: self._run_implicit_context(
                    code=code,
                    language=language,
                    cwd=cwd,
                    env=env,
                    timeout=remaining_timeout,
                ),
            )
        rebound_context = self.connect_context(context)
        if rebound_context.sandbox_id != self._sandbox_id:
            raise create_error(
                "interpreter context belongs to a different sandbox",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        if env is not None:
            raise create_error(
                "env is only supported when creating a context or running without an explicit context",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        return await rebound_context.run(code, cwd=cwd, env=env, timeout=timeout)

    def _bind_context(self, context_id: str, language: str) -> AsyncInterpreterContext:
        gate = self._gate_for(context_id)

        async def run_bound(
            code: str,
            cwd: Optional[str],
            env: Optional[Mapping[str, str]],
            timeout: Optional[float],
        ) -> InterpreterResult:
            if env is not None:
                raise create_error(
                    "env is only supported when creating a context or running without an explicit context",
                    400,
                    codes.BAD_REQUEST,
                    "SDK",
                )
            return await gate.run(
                timeout,
                lambda remaining_timeout: self._run_bound_context(
                    context_id=context_id,
                    language=language,
                    code=code,
                    cwd=cwd,
                    env=env,
                    timeout=remaining_timeout,
                ),
            )

        async def close_bound() -> None:
            await self.delete_context(context_id)

        return AsyncInterpreterContext(self._sandbox_id, context_id, language).bind(run_bound, close_bound)

    def _context_identity(self, context: AsyncInterpreterContext | InterpreterContextJSON) -> tuple[str, str]:
        if isinstance(context, AsyncInterpreterContext):
            sandbox_id = context.sandbox_id
            context_id = context.context_id
            language = context.language
        else:
            sandbox_id = context["sandbox_id"]
            context_id = context["context_id"]
            language = context["language"]
        if sandbox_id != self._sandbox_id:
            raise create_error(
                "interpreter context belongs to a different sandbox",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        return context_id, language

    async def _collect_result(
        self, handle: AsyncProcessHandle, *, timeout: Optional[float], expired: bool = False
    ) -> InterpreterResult:
        wait = await wait_or_interrupt(handle, timeout=timeout, expired=expired)
        logs = await _all_logs(
            handle,
            max_pages=TIMED_OUT_LOG_PAGE_LIMIT if wait.reason == "timed_out" else None,
        )
        stdout = "".join(frame.data for frame in logs if frame.channel == "stdout")
        stderr = "".join(frame.data for frame in logs if frame.channel == "stderr")
        results: list[InterpreterOutput] = []
        error: Optional[InterpreterError] = None
        for frame in logs:
            if frame.channel in ("display", "execute_result"):
                output = _parse_output(frame.data, is_main_result=frame.channel == "execute_result")
                if output is not None:
                    results.append(output)
            elif frame.channel == "error":
                parsed_error = _parse_error(frame.data)
                if parsed_error is not None:
                    error = parsed_error
        return InterpreterResult(
            process=InterpreterProcess(
                handle.id,
                wait.exit_code,
                wait.signal,
                wait.reason,
                raw_reason=wait.raw_reason,
            ),
            results=results,
            logs=InterpreterLogs(stdout=stdout, stderr=stderr),
            error=error,
        )

    async def _run_implicit_context(
        self,
        *,
        code: str,
        language: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        deadline = None if timeout is None or timeout == 0 else _clock.monotonic() + timeout
        handle = await self._process._start_process(
            code=code,
            language=language,
            cwd=cwd,
            env=env,
            keep_logs="on_exit_ttl",
            request_timeout=timeout,
        )
        remaining_timeout = None if deadline is None else max(0.0, deadline - _clock.monotonic())
        return await self._collect_result(
            handle,
            timeout=remaining_timeout,
            expired=remaining_timeout is not None and remaining_timeout <= 0,
        )

    async def _run_bound_context(
        self,
        *,
        context_id: str,
        language: str,
        code: str,
        cwd: Optional[str],
        env: Optional[Mapping[str, str]],
        timeout: Optional[float],
    ) -> InterpreterResult:
        deadline = None if timeout is None or timeout == 0 else _clock.monotonic() + timeout
        handle = await self._process._start_process(
            code=code,
            language=language,
            session_id=context_id,
            cwd=cwd,
            env=env,
            keep_logs="on_exit_ttl",
            request_timeout=timeout,
        )
        remaining_timeout = None if deadline is None else max(0.0, deadline - _clock.monotonic())
        return await self._collect_result(
            handle,
            timeout=remaining_timeout,
            expired=remaining_timeout is not None and remaining_timeout <= 0,
        )

    async def _delete_context_best_effort(self, context_id: str) -> None:
        try:
            await self.delete_context(context_id)
        except DaytonaError:
            return


def _parse_output(payload: str, *, is_main_result: bool) -> Optional[InterpreterOutput]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    data = _mime_bundle(decoded)
    if data is None:
        return None
    return InterpreterOutput(
        data=data,
        is_main_result=is_main_result,
        text=_bundle_string(data, "text/plain"),
        html=_bundle_string(data, "text/html"),
        png=_bundle_string(data, "image/png"),
        svg=_bundle_string(data, "image/svg+xml"),
        json=data.get("application/json"),
    )


def _parse_error(payload: str) -> Optional[InterpreterError]:
    try:
        decoded = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    traceback = decoded.get("traceback")
    return InterpreterError(
        name=str(decoded.get("ename") or ""),
        value=str(decoded.get("evalue") or ""),
        traceback=[str(item) for item in traceback] if isinstance(traceback, list) else [],
    )


def _mime_bundle(decoded: dict[str, object]) -> Optional[JsonObject]:
    if "data" in decoded:
        raw_data = decoded.get("data")
        if isinstance(raw_data, dict):
            return {str(mime): value for mime, value in raw_data.items() if _is_json_value(value)}
        mime = decoded.get("mime")
        if isinstance(mime, str) and _is_json_value(raw_data):
            return {mime: raw_data}
        return None
    data: JsonObject = {}
    for mime, value in decoded.items():
        if _is_json_value(value):
            data[str(mime)] = value
    return data


def _bundle_string(data: Mapping[str, JsonValue], mime: str) -> Optional[str]:
    value = data.get(mime)
    return value if isinstance(value, str) else None


def _is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Structural check for a decoded JSON value, iterative on purpose.

    The payload is sandbox-controlled: a result nested a few hundred levels
    deep parses fine in the C JSON decoder but would overflow a recursive
    checker and surface as a raw ``RecursionError`` from ``run_code()``.
    """
    pending: list[object] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, (str, int, float, bool)) or item is None:
            continue
        if isinstance(item, list):
            pending.extend(item)
            continue
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    return False
                pending.append(nested)
            continue
        return False
    return True
