# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Awaitable, Callable, Mapping, Optional

from . import _clock
from . import _codes as codes
from ._core import (
    RequestSpec,
    bounded_request_timeout,
    expect_object,
    is_json_value,
    loads_json,
    path_segment,
    timeout_millis_from_seconds,
    wait_budget,
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


def _json_identity(value: object) -> Optional[tuple[str, str, str]]:
    """``(sandbox_id, context_id, language)`` of a serialized context.

    Reads only the camelCase ``to_json()`` shape, shared with the TypeScript SDK.
    """
    if not isinstance(value, Mapping):
        return None
    sandbox_id = value.get("sandboxId")
    context_id = value.get("contextId")
    language = value.get("language")
    if isinstance(sandbox_id, str) and isinstance(context_id, str) and isinstance(language, str):
        return sandbox_id, context_id, language
    return None


def _session_identity(value: object, *, requested_id: Optional[str] = None) -> tuple[str, str]:
    """The ``id`` and ``kernel`` of a ``SessionDto``, the only fields the SDK
    reads (as the TypeScript SDK checks them). A missing or empty id is
    malformed; when the caller chose the context id, the message names it so
    the context can be cleaned up."""
    description = "session response"
    if not isinstance(value, dict):
        raise create_error(f"expected {description}", 400, codes.BAD_REQUEST, "SDK")
    context_id = value.get("id")
    if not isinstance(context_id, str) or not context_id:
        problem = "is required" if context_id in (None, "") else "must be a string"
        context = (
            "" if requested_id is None else f" (context {json.dumps(requested_id)} may have been created)"
        )
        raise create_error(f"expected {description}: id {problem}{context}", 400, codes.BAD_REQUEST, "SDK")
    kernel = value.get("kernel")
    if not isinstance(kernel, str):
        problem = "is required" if kernel is None else "must be a string"
        raise create_error(f"expected {description}: kernel {problem}", 400, codes.BAD_REQUEST, "SDK")
    return context_id, kernel


def _timed_out_result() -> InterpreterResult:
    return InterpreterResult(
        process=InterpreterProcess(reason="timed_out"),
        results=(),
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
    """Sandbox interpreter namespace: run code in language kernels."""

    def __init__(self, _sandbox_id: str, _transport: AsyncTransport, _process: AsyncProcessService) -> None:
        self._sandbox_id = _sandbox_id
        self._transport = _transport
        self._process = _process
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
    ) -> AsyncInterpreterContext:
        """Create an interpreter context: kernel state of its own.

        ``cwd`` and ``env`` apply to every run in the context.
        """
        return await self._create_context(language=language, context_id=context_id, cwd=cwd, env=env)

    async def _create_context(
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
        payload = expect_object(
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
        created_id, _kernel = _session_identity(payload, requested_id=context_id)
        return self._bind_context(created_id, language)

    def connect_context(
        self,
        context: str | AsyncInterpreterContext | InterpreterContextJSON,
        /,
        *,
        language: str | None = None,
    ) -> AsyncInterpreterContext:
        """Reconnect a context by id (with ``language``), handle or ``to_json()``, without a request."""
        if isinstance(context, (AsyncInterpreterContext, Mapping)):
            return self._bind_context(*self._context_identity(context))
        if language is None:
            raise create_error(
                "language is required when reconnecting an interpreter context by id",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        return self._bind_context(context, language)

    async def list_contexts(self) -> list[AsyncInterpreterContext]:
        payload = expect_object(await self._transport.send(RequestSpec("GET", "/sessions")))
        items = payload.get("items")
        if not isinstance(items, list):
            problem = "is required" if items is None else "must be a list"
            raise create_error(
                f"expected session list response: items {problem}", 400, codes.BAD_REQUEST, "SDK"
            )
        contexts: list[AsyncInterpreterContext] = []
        for item in items:
            context_id, kernel = _session_identity(item)
            if kernel == SHELL_SESSION_KERNEL:
                continue
            contexts.append(self._bind_context(context_id, kernel))
        return contexts

    async def delete_context(
        self, context: AsyncInterpreterContext | InterpreterContextJSON | str, /
    ) -> None:
        if isinstance(context, (AsyncInterpreterContext, Mapping)):
            context_id, _ = self._context_identity(context)
        else:
            context_id = context
        if not isinstance(context_id, str) or not context_id:
            raise create_error(
                "interpreter context id must be a non-empty string", 400, codes.BAD_REQUEST, "SDK"
            )
        try:
            await self._transport.send(
                RequestSpec("DELETE", f"/sessions/{path_segment(context_id, 'interpreter context id')}")
            )
        except DaytonaError as error:
            if error.code == codes.SESSION_NOT_FOUND:
                self._evict_gate_when_drained(context_id)
            raise
        self._evict_gate_when_drained(context_id)

    async def run(
        self,
        code: str,
        /,
        *,
        language: Optional[str] = None,
        context: str | AsyncInterpreterContext | InterpreterContextJSON | None = None,
        isolated: bool = False,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> InterpreterResult:
        """Execute code and return the parsed interpreter result.

        Without ``context`` (or ``isolated``) runs share kernel state per
        ``language`` (default ``"python"``). ``context`` selects an interpreter
        context by handle, ``to_json()`` identity or id; an id needs
        ``language``. ``isolated=True`` runs in a throwaway context. ``env``
        applies to context-free and isolated runs; a context takes its
        environment when it is created.

        A rehydrated ``kind='code'`` process handle can still replay logs and
        wait/kill, but it does not reconstruct this decomposed result on
        another worker. A vanished explicit context surfaces the API's typed
        ``SESSION_NOT_FOUND`` error; callers decide whether to recreate the
        context and what to rerun. ``timeout`` is the caller wait budget in
        seconds; ``0`` waits unbounded.
        """
        timeout = wait_budget(timeout)
        if isolated and context is not None:
            raise create_error(
                "isolated runs cannot also target an explicit interpreter context",
                400,
                codes.BAD_REQUEST,
                "SDK",
            )
        if context is not None:
            rebound_context = self.connect_context(context, language=language)
            if language is not None and language != rebound_context.language:
                raise create_error(
                    f"language {language!r} does not match the context's language {rebound_context.language!r}",
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
            return await rebound_context.run(code, cwd=cwd, timeout=timeout)
        resolved_language = language if language is not None else "python"
        if isolated:
            # Reject an invalid budget before any request is issued, like the
            # other run paths do.
            timeout_millis_from_seconds(timeout)
            # The throwaway session's POST /sessions is part of this run and
            # spends the same caller budget as the execution itself.
            started = _clock.monotonic()
            context_id = _isolated_context_id(resolved_language)
            cleanup_id = context_id
            try:
                isolated_context = await self._create_context(
                    language=resolved_language,
                    context_id=context_id,
                    cwd=cwd,
                    env=env,
                    request_timeout=timeout,
                )
                # The response is the truth: run in and clean up the id the
                # sandbox returned, even if it differs from the requested one.
                cleanup_id = isolated_context.context_id
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
                await self._delete_context_best_effort(cleanup_id)
        gate = self._gate_for(_implicit_context_id(resolved_language))
        return await gate.run(
            timeout,
            lambda remaining_timeout: self._run_implicit_context(
                code=code,
                language=resolved_language,
                cwd=cwd,
                env=env,
                timeout=remaining_timeout,
            ),
        )

    def _bind_context(self, context_id: str, language: str) -> AsyncInterpreterContext:
        # Every context identity round-trips through to_json(): a non-string id
        # or language would be written in a shape neither SDK reads back.
        if not isinstance(context_id, str) or not context_id:
            raise create_error(
                "interpreter context id must be a non-empty string", 400, codes.BAD_REQUEST, "SDK"
            )
        # Closing deletes the context through its path id, so one that could
        # not be closed (blank, `.`/`..`, a surrogate) is not bound either.
        path_segment(context_id, "interpreter context id")
        if not isinstance(language, str) or not language:
            raise create_error(
                "interpreter context language must be a non-empty string", 400, codes.BAD_REQUEST, "SDK"
            )
        gate = self._gate_for(context_id)

        async def run_bound(
            code: str,
            cwd: Optional[str],
            timeout: Optional[float],
        ) -> InterpreterResult:
            return await gate.run(
                wait_budget(timeout),
                lambda remaining_timeout: self._run_bound_context(
                    context_id=context_id,
                    language=language,
                    code=code,
                    cwd=cwd,
                    timeout=remaining_timeout,
                ),
            )

        async def close_bound() -> None:
            await self.delete_context(context_id)

        return AsyncInterpreterContext(self._sandbox_id, context_id, language)._bind(run_bound, close_bound)

    def _context_identity(
        self, context: AsyncInterpreterContext | InterpreterContextJSON | Mapping[str, object]
    ) -> tuple[str, str]:
        if isinstance(context, AsyncInterpreterContext):
            sandbox_id = context.sandbox_id
            context_id = context.context_id
            language = context.language
        else:
            identity = _json_identity(context)
            if identity is None:
                raise create_error(
                    "interpreter context identity needs string sandboxId, contextId and language",
                    400,
                    codes.BAD_REQUEST,
                    "SDK",
                )
            sandbox_id, context_id, language = identity
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
                process_id=handle.id,
                reason=wait.reason,
                exit_code=wait.exit_code,
                signal=wait.signal,
                raw_reason=wait.raw_reason,
            ),
            results=tuple(results),
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
        timeout: Optional[float],
    ) -> InterpreterResult:
        deadline = None if timeout is None or timeout == 0 else _clock.monotonic() + timeout
        handle = await self._process._start_process(
            code=code,
            language=language,
            session_id=context_id,
            cwd=cwd,
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
        decoded = loads_json(payload, "interpreter output")
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
        decoded = loads_json(payload, "interpreter error")
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    traceback = decoded.get("traceback")
    return InterpreterError(
        name=str(decoded.get("ename") or ""),
        value=str(decoded.get("evalue") or ""),
        traceback=tuple(str(item) for item in traceback) if isinstance(traceback, list) else (),
    )


def _mime_bundle(decoded: dict[str, object]) -> Optional[JsonObject]:
    if "data" in decoded:
        raw_data = decoded.get("data")
        if isinstance(raw_data, dict):
            return {str(mime): value for mime, value in raw_data.items() if is_json_value(value)}
        mime = decoded.get("mime")
        if isinstance(mime, str) and is_json_value(raw_data):
            return {mime: raw_data}
        return None
    data: JsonObject = {}
    for mime, value in decoded.items():
        if is_json_value(value):
            data[str(mime)] = value
    return data


def _bundle_string(data: Mapping[str, JsonValue], mime: str) -> Optional[str]:
    value = data.get(mime)
    return value if isinstance(value, str) else None
