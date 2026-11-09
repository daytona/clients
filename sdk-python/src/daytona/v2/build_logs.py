# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded, resumable build-log observation (no transcript retention)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import inspect
import json
import random
import re
import time
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Literal, Mapping, TypeGuard
from urllib.parse import unquote, urlsplit

import anyio
from pydantic import BaseModel
from typing_extensions import Unpack

from . import _clock
from ._core import (
    JSON_MAX_DEPTH,
    RequestSpec,
    _nested_deeper_than,
    parse_error_payload,
    query_text,
    wait_budget_seconds,
)
from ._gen.api import (
    BuildLogChunk,
    BuildLogDone,
    BuildLogError,
    BuildLogGap,
    BuildLogSourceChanged,
    BuildLogState,
    BuildLogsUrl,
    BuildLogUnavailable,
)
from ._gen.api import (
    BuildLogDoneReason as BuildLogDoneReasonEnum,
)
from ._gen.api import (
    BuildLogGapReason as BuildLogGapReasonEnum,
)
from ._transport import AsyncResponseStream, AsyncTransport
from .errors import (
    ClientTimeoutError,
    ConnectionError,
    DaytonaError,
    _attach_interrupt_error,
    _ErrorFields,
    create_error,
)
from .types import JsonValue

BuildLogDoneReason = Literal["terminal", "deadline", "not_applicable", "resource_deleted", "unknown"]
BuildLogGapReason = Literal["truncated", "unknown"]
_BUILD_LOG_DONE_REASON_VALUES = tuple(
    str(member.value) for member in BuildLogDoneReasonEnum if member.name != "UNKNOWN_DEFAULT_OPEN_API"
)
_BUILD_LOG_GAP_REASON_VALUES = tuple(
    str(member.value) for member in BuildLogGapReasonEnum if member.name != "UNKNOWN_DEFAULT_OPEN_API"
)


def _is_build_log_done_reason_known(
    value: str,
) -> TypeGuard[Literal["terminal", "deadline", "not_applicable", "resource_deleted"]]:
    return value in _BUILD_LOG_DONE_REASON_VALUES


def _is_build_log_gap_reason_known(value: str) -> TypeGuard[Literal["truncated"]]:
    return value in _BUILD_LOG_GAP_REASON_VALUES


def _build_log_done_reason_or_unknown(value: str) -> BuildLogDoneReason:
    return value if _is_build_log_done_reason_known(value) else "unknown"


def _build_log_gap_reason_or_unknown(value: str) -> BuildLogGapReason:
    return value if _is_build_log_gap_reason_known(value) else "unknown"


@dataclass(frozen=True, kw_only=True)
class BuildLogFollowResult:
    status: str
    reason: BuildLogDoneReason
    output_complete: bool
    cursor: str | None = None
    raw_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class BuildLogStateEvent:
    status: str
    error: str | None = None
    deadline_at: datetime | None = None
    type: Literal["state"] = "state"


@dataclass(frozen=True, kw_only=True)
class BuildLogChunkEvent:
    data_base64: str
    start_offset: str
    next_offset: str
    cursor: str
    type: Literal["log"] = "log"


@dataclass(frozen=True, kw_only=True)
class BuildLogGapEvent:
    requested_offset: str
    first_available_offset: str
    dropped_bytes: str
    cursor: str
    reason: BuildLogGapReason = "truncated"
    type: Literal["gap"] = "gap"
    raw_reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class BuildLogUnavailableEvent:
    reason: str
    type: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True, kw_only=True)
class BuildLogSourceChangedEvent:
    reason: str
    type: Literal["source_changed"] = "source_changed"


@dataclass(frozen=True, kw_only=True)
class BuildLogDoneEvent:
    result: BuildLogFollowResult
    type: Literal["done"] = "done"


@dataclass(frozen=True, kw_only=True)
class BuildLogErrorEvent:
    error: str
    code: str
    retryable: bool
    trace_id: str | None = None
    type: Literal["error"] = "error"


BuildLogEvent = (
    BuildLogStateEvent
    | BuildLogChunkEvent
    | BuildLogGapEvent
    | BuildLogUnavailableEvent
    | BuildLogSourceChangedEvent
    | BuildLogDoneEvent
    | BuildLogErrorEvent
)

# Callbacks may return an awaitable, which is awaited before the next event —
# the same convention as the filesystem watch callbacks.
BuildLogsCallback = Callable[[str], "None | Awaitable[None]"]
"""Receives decoded build output; may return an awaitable, awaited before the next event."""
BuildLogEventCallback = Callable[[BuildLogEvent], "None | Awaitable[None]"]
"""Receives each typed :data:`BuildLogEvent`; may return an awaitable, awaited before the next event."""


async def _invoke(result: None | Awaitable[None]) -> None:
    if inspect.isawaitable(result):
        await result


class BuildLogInterruptedError(DaytonaError):
    """Observation failed; cursor is the last successfully delivered checkpoint.

    A callback failure is never retried. Its cause may have partially applied
    application effects; persist output and cursor together before resuming.
    """

    def __init__(
        self,
        message: str,
        *,
        cursor: str | None = None,
        status_code: int | None = None,
        code: str | None = None,
        source: str | None = None,
        **fields: Unpack[_ErrorFields],
    ) -> None:
        # Omitted or None: code BUILD_LOG_INTERRUPTED, source SDK (as in TypeScript).
        super().__init__(
            message,
            status_code=status_code,
            code=code if code is not None else "BUILD_LOG_INTERRUPTED",
            source=source if source is not None else "SDK",
            **fields,
        )
        self.cursor = cursor


class _ProtocolError(Exception):
    pass


class _Retry(Exception):
    def __init__(self, message: str, retry_after: float = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _CallbackError(Exception):
    pass


_MAX_EVENT = 128 * 1024
# Bound this SDK string API to 2 MiB of response-body bytes so a misbehaving
# endpoint cannot turn a one-shot read into an unbounded allocation.
_MAX_ONE_SHOT_BYTES = 2 * 1024 * 1024
_WIRE_MODELS: Mapping[str, type[BaseModel]] = {
    "state": BuildLogState,
    "log": BuildLogChunk,
    "gap": BuildLogGap,
    "unavailable": BuildLogUnavailable,
    "source_changed": BuildLogSourceChanged,
    "done": BuildLogDone,
    "error": BuildLogError,
}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _ProtocolError("expected JSON object")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _json(text: str | bytes) -> object:
    """``json.loads`` for build-log JSON: the non-standard constants, and a
    value nested deeper than ``JSON_MAX_DEPTH`` (the limit both SDKs apply to
    every JSON body), are invalid JSON (``ValueError``)."""
    value: JsonValue = json.loads(text, parse_constant=_invalid_json_constant)
    if _nested_deeper_than(value, JSON_MAX_DEPTH):
        raise ValueError("build log JSON is nested too deeply")
    return value


def _invalid_json_constant(_value: str) -> object:
    # Python's JSON decoder otherwise accepts NaN/Infinity, unlike JSON and the
    # TypeScript peer. Even unknown fields must remain valid JSON.
    raise _ProtocolError("invalid JSON numeric constant")


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise _ProtocolError("expected string")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise _ProtocolError("expected boolean")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _offset(value: object) -> int:
    text = _text(value)
    if not re.fullmatch(r"0|[1-9][0-9]{0,19}", text) or int(text) > 2**64 - 1:
        raise _ProtocolError("invalid uint64 offset")
    return int(text)


def _cursor_ok(text: str) -> bool:
    # Cursors are opaque. One rule in both SDKs and both directions: non-empty,
    # bounded, and no C0 control character or DEL; their encoding and contents
    # are not inferred.
    return bool(text) and len(text) <= 2048 and re.search(r"[\x00-\x1f\x7f]", text) is None


def _cursor(value: object) -> str:
    """A server-sent cursor: one that breaks the rule is the protocol error."""
    text = _text(value)
    if not _cursor_ok(text):
        raise _ProtocolError("invalid cursor")
    return text


def _caller_cursor(value: object) -> str:
    """A caller's resume cursor: one that breaks the rule is BadRequestError
    naming it, before any request, as in TypeScript. It is sent as a query
    value, so a lone surrogate is refused too."""
    if not isinstance(value, str) or not _cursor_ok(value):
        raise create_error(
            "cursor must be a non-empty string of at most 2048 characters with no control characters",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return query_text(value, "cursor")


def _bytes(value: object) -> bytes:
    text = _text(value)
    if len(text) > 87384:
        raise _ProtocolError("oversized log chunk")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise _ProtocolError("invalid base64") from exc
    if len(raw) > 65536 or base64.b64encode(raw).decode() != text:
        raise _ProtocolError("invalid log chunk")
    return raw


class _SseDecoder:
    """SSE byte framing: caps complete AND unfinished events before decoding.

    Sans-I/O and incremental: ``feed`` one transport chunk at a time. CR, LF
    and CRLF are supported across chunk boundaries. EOF deliberately drops the
    partial event; it must never commit an unacknowledged event ID.
    """

    def __init__(self) -> None:
        self._line = bytearray()
        self._size = 0
        self._after_cr = False
        self._first_line = True
        self._event = "message"
        self._ident: str | None = None
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> Iterator[tuple[str, str | None, dict[str, object]]]:
        # A generator, so events earlier in a chunk are delivered before a
        # framing error later in the same chunk is raised.
        for byte in chunk:
            if self._after_cr and byte == 10 and self._size == 0:
                # The preceding blank CR already dispatched the event. Its
                # possible LF was reserved before dispatch, not charged to the
                # next event (which may be in a different transport chunk).
                self._after_cr = False
                continue
            self._size += 1
            if self._size > _MAX_EVENT or (byte == 13 and self._size == _MAX_EVENT):
                raise _ProtocolError("SSE event exceeds 128 KiB")
            if self._after_cr:
                self._after_cr = False
                if byte == 10:
                    continue
            if byte not in (10, 13):
                self._line.append(byte)
                continue
            self._after_cr = byte == 13
            try:
                text = self._line.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise _ProtocolError("invalid SSE UTF-8") from exc
            if self._first_line:
                text = text.removeprefix("\ufeff")
                self._first_line = False
            self._line.clear()
            if not text:
                if self._data:
                    try:
                        payload = _object(_json("\n".join(self._data)))
                    except (ValueError, RecursionError) as exc:
                        raise _ProtocolError("invalid event JSON") from exc
                    yield self._event, self._ident, payload
                elif self._ident is not None:
                    raise _ProtocolError("event ID without data")
                self._event, self._ident, self._data, self._size = "message", None, [], 0
                continue
            field, sep, value = text.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                self._event = value
            elif field == "id":
                if self._ident is not None or not sep:
                    raise _ProtocolError("duplicate or missing SSE ID")
                self._ident = value
            elif field == "data":
                self._data.append(value)


def _messages(chunks: Iterable[bytes]) -> Iterator[tuple[str, str | None, dict[str, object]]]:
    decoder = _SseDecoder()
    for chunk in chunks:
        yield from decoder.feed(chunk)


class _Observer:
    """Sans-I/O protocol/decoder state, retained through reconnections."""

    def __init__(
        self, on_logs: BuildLogsCallback, on_event: BuildLogEventCallback | None, cursor: str | None
    ) -> None:
        self.on_logs, self.on_event = on_logs, on_event
        self.cursor = _cursor(cursor) if cursor is not None else None
        # A resumed stream establishes its starting offset with its first event.
        self.offset: int | None = None if cursor is not None else 0
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.gap = False

    async def _emit(self, event: BuildLogEvent, text: str = "") -> None:
        # Callbacks may be plain functions or return an awaitable (the
        # filesystem watch convention); either way they run on this loop.
        try:
            if text:
                await _invoke(self.on_logs(text))
            if self.on_event:
                await _invoke(self.on_event(event))
            elif event.type in ("state", "gap", "unavailable", "source_changed", "error"):
                await _invoke(self.on_logs(f"[build logs: {event.type}] {event}\n"))
        except Exception as exc:
            raise _CallbackError("build log callback failed") from exc

    async def accept(
        self, event: str, ident: str | None, data: dict[str, object]
    ) -> BuildLogFollowResult | None:
        model = _WIRE_MODELS.get(event)
        if model is None:
            raise _ProtocolError("unknown build log event")
        try:
            model.model_validate(data)
        except ValueError as exc:
            raise _ProtocolError("invalid build log event schema") from exc
        if event not in ("log", "gap", "done") and ident is not None:
            raise _ProtocolError("control event must not advance cursor")
        if event == "state":
            status = _text(data.get("status"))
            deadline = _optional_text(data.get("deadline_at"))
            error = _optional_text(data.get("error"))
            # The build deadline is metadata, not the caller's timeout.
            try:
                expires = datetime.fromisoformat(deadline.replace("Z", "+00:00")) if deadline else None
                if expires is not None and expires.tzinfo is None:
                    raise ValueError("missing timezone")
            except ValueError as exc:
                raise _ProtocolError("invalid build deadline") from exc
            await self._emit(BuildLogStateEvent(status=status, error=error, deadline_at=expires))
        elif event == "log":
            raw = _bytes(data.get("data_base64"))
            start, end = _offset(data.get("start_offset")), _offset(data.get("next_offset"))
            if not raw or end - start != len(raw) or (self.offset is not None and start != self.offset):
                raise _ProtocolError("noncontiguous log offsets")
            checkpoint = _cursor(ident)
            await self._emit(
                BuildLogChunkEvent(
                    data_base64=_text(data["data_base64"]),
                    start_offset=str(start),
                    next_offset=str(end),
                    cursor=_text(ident),
                ),
                self.decoder.decode(raw),
            )
            self.cursor, self.offset = checkpoint, end
        elif event == "gap":
            requested, first, dropped = (
                _offset(data.get(key))
                for key in ("requested_offset", "first_available_offset", "dropped_bytes")
            )
            reason = _text(data.get("reason"))
            if (
                first <= requested
                or first - requested != dropped
                or (self.offset is not None and requested != self.offset)
            ):
                raise _ProtocolError("invalid log gap")
            checkpoint = _cursor(ident)
            carry = self.decoder.decode(b"", final=True)
            await self._emit(
                BuildLogGapEvent(
                    requested_offset=str(requested),
                    first_available_offset=str(first),
                    dropped_bytes=str(dropped),
                    cursor=_text(ident),
                    reason=_build_log_gap_reason_or_unknown(reason),
                    raw_reason=reason,
                ),
                carry,
            )
            self.decoder.reset()
            self.cursor, self.offset, self.gap = checkpoint, first, True
        elif event == "unavailable":
            await self._emit(BuildLogUnavailableEvent(reason=_text(data.get("reason"))))
        elif event == "source_changed":
            reason = _text(data.get("reason"))
            await self._emit(BuildLogSourceChangedEvent(reason=reason))
            raise BuildLogInterruptedError(reason, cursor=self.cursor, code="BUILD_LOG_SOURCE_CHANGED")
        elif event == "error":
            error = _text(data.get("error"))
            code, retryable = _text(data.get("code")), _bool(data.get("retryable"))
            await self._emit(
                BuildLogErrorEvent(
                    error=error, code=code, retryable=retryable, trace_id=_optional_text(data.get("trace_id"))
                )
            )
            if retryable:
                raise _Retry(error)
            raise BuildLogInterruptedError(error, cursor=self.cursor, code=code)
        elif event == "done":
            status, reason = _text(data.get("status")), _text(data.get("reason"))
            complete = _bool(data.get("output_complete"))
            final_cursor = _optional_text(data.get("cursor"))
            checkpoint_cursor = self.cursor
            for checkpoint in (ident, final_cursor):
                if checkpoint is not None and checkpoint != checkpoint_cursor:
                    if checkpoint_cursor is not None:
                        raise _ProtocolError("done advanced cursor without bytes")
                    checkpoint_cursor = _cursor(checkpoint)
            result = BuildLogFollowResult(
                status=status,
                reason=_build_log_done_reason_or_unknown(reason),
                output_complete=complete and not self.gap,
                cursor=checkpoint_cursor,
                raw_reason=reason,
            )
            await self._emit(BuildLogDoneEvent(result=result), self.decoder.decode(b"", final=True))
            self.cursor = checkpoint_cursor
            return result
        else:
            raise _ProtocolError("unknown build log event")
        return None


def _resource_identity(value: str, sandbox: bool) -> tuple[str, str | None] | None:
    uuid = (
        value.removeprefix("urn:uuid:")
        if value.startswith("urn:uuid:")
        else (value[1:-1] if value.startswith("{") and value.endswith("}") else value)
    )
    if re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", uuid, re.IGNORECASE):
        return uuid.replace("-", "").lower(), None
    if not sandbox:
        return None
    body, separator, region = value.partition("-")
    # The SDK accepts region tags of 1-64 ASCII [A-Za-z0-9_-] characters.
    if not body or len(body) > 22 or separator and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", region, re.ASCII):
        return None
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for char in body:
        digit = alphabet.find(char)
        if digit < 0:
            return None
        number = number * 58 + digit
    zeros = len(body) - len(body.lstrip("1"))
    if zeros + (number.bit_length() + 7) // 8 != 16:
        return None
    return f"{number:032x}", region if separator else None


def _same_resource(collection: str, requested: str, actual: str) -> bool:
    if requested == actual:
        return True
    sandbox_collection = collection == "sandboxes"
    left = _resource_identity(requested, sandbox_collection)
    right = _resource_identity(actual, sandbox_collection)
    # Discovery may return a region-qualified ID or an equivalent bare UUID.
    # Preserve the region constraint whenever both sides carry one.
    return (
        left is not None
        and right is not None
        and left[0] == right[0]
        and (left[1] is None or right[1] is None or left[1] == right[1])
    )


def _destination(api: AsyncTransport, payload: object, discovery_path: str) -> AsyncTransport:
    try:
        data = _object(BuildLogsUrl.model_validate(payload).model_dump(mode="json"))
    except ValueError as exc:
        raise _ProtocolError("invalid build log discovery") from exc
    url = _text(data.get("url"))
    if not url or len(url) > 4096 or re.search(r"[\x00-\x20\x7f\\]", url) or "?" in url or "#" in url:
        raise _ProtocolError("invalid build log URL")
    try:
        target = urlsplit(url)
        if (
            target.scheme not in ("http", "https")
            or not target.hostname
            or target.username is not None
            or target.password is not None
        ):
            raise ValueError("invalid destination")
        _ = target.port
        requested = re.fullmatch(r"/([^/]+)/([^/]+)/build-logs-url", discovery_path)
        actual = re.search(r"/build-logs/[^/%]+/([^/%]+)$", target.path)
        if requested is None or actual is None:
            raise ValueError("unexpected build log route")
        if not _same_resource(requested[1], unquote(requested[2]), actual[1]):
            raise ValueError("unexpected build log route")
    except ValueError as exc:
        raise _ProtocolError("invalid build log URL") from exc
    return api.scoped(url, api.token)


def _retry_after(headers: Mapping[str, str]) -> float:
    value = headers.get("retry-after", "")
    if re.fullmatch(r"[0-9]+", value):
        return float(value)
    try:
        return max(0, parsedate_to_datetime(value).timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return 0


async def _http_error(status: int, chunks: AsyncIterator[bytes]) -> DaytonaError:
    body = bytearray()
    try:
        async for chunk in chunks:
            if len(body) + len(chunk) > _MAX_EVENT:
                return create_error(
                    "oversized build log error response",
                    status,
                    "BUILD_LOG_PROTOCOL_ERROR",
                )
            body.extend(chunk)
    except (ConnectionError, ClientTimeoutError):
        # A known 401/403/etc remains terminal even if its diagnostic body fails.
        return create_error(f"build log HTTP {status}", status)
    try:
        payload = _object(_json(body))
    except (ValueError, RecursionError, _ProtocolError):
        return create_error(f"build log HTTP {status}", status)
    # No usable prose (absent, empty or malformed) keeps the build-log status
    # fallback, still dispatched on code/source — and never raises on a bad type.
    message, error = payload.get("message"), payload.get("error")
    if not (isinstance(message, str) and message) and not (isinstance(error, str) and error):
        payload = {**payload, "message": f"build log HTTP {status}"}
    return parse_error_payload(status, payload)


def _budget(deadline: float | None) -> float | None:
    return None if deadline is None else deadline - _clock.monotonic()


async def _finite_http_error(stream: AsyncResponseStream, deadline: float | None) -> DaytonaError:
    """Bound diagnostic bodies without replacing a known HTTP denial by retry."""
    budget = _budget(deadline)
    if budget is not None and budget <= 0:
        return create_error(f"build log HTTP {stream.status_code}", stream.status_code)
    with anyio.move_on_after(budget):
        async with aclosing(stream.iter_bytes()) as chunks:
            return await _http_error(stream.status_code, chunks)
    # The deadline cut the body short: the status is still the answer.
    return create_error(f"build log HTTP {stream.status_code}", stream.status_code)


async def _read_body(stream: AsyncResponseStream, limit: int, message: str) -> bytes:
    body = bytearray()
    async with aclosing(stream.iter_bytes()) as chunks:
        async for chunk in chunks:
            if len(chunk) > limit - len(body):
                raise _ProtocolError(message)
            body.extend(chunk)
    return bytes(body)


async def _discover(
    api: AsyncTransport,
    discovery_path: str,
    timeout: float | None,
    *,
    retry: bool = True,
) -> object:
    started = _clock.monotonic()
    deadline = started + timeout if timeout else None
    stream = await api.open_exclusive_stream(RequestSpec("GET", discovery_path, timeout=timeout))
    try:
        if not 200 <= stream.status_code < 300:
            if retry and (stream.status_code == 429 or stream.status_code >= 500):
                raise _Retry(f"build log discovery HTTP {stream.status_code}", _retry_after(stream.headers))
            raise await _finite_http_error(stream, deadline)
        budget = _budget(deadline)
        if budget is not None and budget <= 0:
            raise ClientTimeoutError("build log discovery timed out")
        body = b""
        # httpx read timeouts are idle bounds; the whole body gets one budget.
        with anyio.move_on_after(budget) as scope:
            body = await _read_body(stream, _MAX_EVENT, "oversized build log discovery")
        if scope.cancelled_caught or (deadline is not None and _clock.monotonic() >= deadline):
            raise ClientTimeoutError("build log discovery timed out")
        try:
            # Strict UTF-8, as TypeScript decodes it: invalid UTF-8 and a leading
            # BOM are not JSON (json.loads on bytes would strip a BOM and accept
            # UTF-16 / UTF-32).
            return _object(_json(body.decode("utf-8")))
        except (ValueError, RecursionError) as exc:
            raise _ProtocolError("invalid build log discovery JSON") from exc
    finally:
        await stream.close()


def _read_timeout_error() -> ClientTimeoutError:
    return ClientTimeoutError(
        "build log read timed out", status_code=408, code="REQUEST_TIMEOUT", source="SDK"
    )


async def read_build_logs(api: AsyncTransport, discovery_path: str) -> str:
    """One-shot text snapshot, using only explicit API discovery."""
    try:
        configured = api.request_timeout_seconds or None
        discovery = await _discover(api, discovery_path, configured, retry=False)
        target = _destination(api, discovery, discovery_path)
        params: dict[str, JsonValue] = {"follow": "false", "format": "text"}
        request_deadline = _clock.monotonic() + configured if configured else None
        stream = await target.open_exclusive_stream(RequestSpec("GET", "", params=params, timeout=configured))
        try:
            if not 200 <= stream.status_code < 300:
                raise await _finite_http_error(stream, request_deadline)
            budget = _budget(request_deadline)
            if budget is not None and budget <= 0:
                raise _read_timeout_error()
            body = b""
            # httpx read timeouts are idle bounds. A response that keeps sending
            # bytes must still finish within this request's whole-body budget.
            with anyio.move_on_after(budget) as scope:
                body = await _read_body(
                    stream, _MAX_ONE_SHOT_BYTES, "one-shot build log response exceeds 2 MiB"
                )
            if scope.cancelled_caught or (
                request_deadline is not None and _clock.monotonic() >= request_deadline
            ):
                raise _read_timeout_error()
            # Preserve a leading BOM as text, matching the streaming follower
            # and TypeScript reader; replace malformed/incomplete UTF-8.
            return body.decode("utf-8", errors="replace")
        except (ConnectionError, ClientTimeoutError):
            # A connection failure at or after our deadline is that deadline.
            if request_deadline is not None and _clock.monotonic() >= request_deadline:
                raise _read_timeout_error() from None
            raise
        finally:
            await stream.close()
    except _ProtocolError as exc:
        raise DaytonaError(str(exc), code="BUILD_LOG_PROTOCOL_ERROR", source="SDK") from exc


async def _sleep(seconds: float) -> None:
    # Reconnect back-off; a seam so tests never patch asyncio's own sleep.
    await asyncio.sleep(seconds)


async def follow_build_logs(
    api: AsyncTransport,
    discovery_path: str,
    *,
    on_logs: BuildLogsCallback,
    on_event: BuildLogEventCallback | None = None,
    cursor: str | None = None,
    timeout: float | None = None,
) -> BuildLogFollowResult:
    # 0, None or above the timer maximum (a huge integer included) is no deadline.
    if timeout is not None:
        timeout = wait_budget_seconds(timeout) or None
    # Invalid arguments are BadRequestError before any request, as in TypeScript.
    if not callable(on_logs):
        raise create_error("on_logs must be a callable", 400, "BAD_REQUEST", "SDK")
    if on_event is not None and not callable(on_event):
        raise create_error("on_event must be a callable when given", 400, "BAD_REQUEST", "SDK")
    if cursor is not None:
        cursor = _caller_cursor(cursor)
    caller_deadline = _clock.monotonic() + timeout if timeout else None
    observer: _Observer | None = None
    try:
        state = _Observer(on_logs, on_event, cursor)
        observer = state
        configured = api.request_timeout_seconds

        def timed_out() -> BuildLogInterruptedError:
            return BuildLogInterruptedError(
                "build log observation timed out",
                cursor=state.cursor,
                code="REQUEST_TIMEOUT",
            )

        def remaining() -> float | None:
            # Build deadlines are reported through stream events. Only the
            # caller's timeout limits how long this client observes the stream.
            budget = _budget(caller_deadline)
            if budget is not None and budget <= 0:
                raise timed_out()
            return budget

        def request_timeout() -> float | None:
            budget = remaining()
            values = [value for value in (budget, configured or None) if value is not None]
            return min(values) if values else None

        target: AsyncTransport | None = None
        failures = 0
        while True:
            started = _clock.monotonic()
            healthy = False
            stream: AsyncResponseStream | None = None
            delay = 0.0
            # The caller's observation budget spans discovery, headers, body
            # reads and callbacks of this attempt; expiry cancels the attempt,
            # which releases (and closes the exclusive client of) its response.
            with anyio.move_on_after(remaining()) as attempt:
                try:
                    if target is None:
                        discovery = await _discover(api, discovery_path, request_timeout())
                        target = _destination(api, discovery, discovery_path)
                    params: dict[str, JsonValue] = {"follow": "true", "format": "sse"}
                    if state.cursor is not None:
                        params["cursor"] = state.cursor
                    header_timeout = request_timeout()
                    request_deadline = _clock.monotonic() + header_timeout if header_timeout else None
                    stream = await target.open_exclusive_stream(
                        RequestSpec(
                            "GET",
                            "",
                            params=params,
                            timeout=header_timeout,
                            unbounded_read=True,
                            headers={"Accept": "text/event-stream", "Accept-Encoding": "identity"},
                        )
                    )
                    if not 200 <= stream.status_code < 300:
                        if stream.status_code == 429 or stream.status_code >= 500:
                            raise _Retry(f"build log HTTP {stream.status_code}", _retry_after(stream.headers))
                        raise await _finite_http_error(stream, request_deadline)
                    if (
                        stream.headers.get("content-type", "").split(";")[0].strip().lower()
                        != "text/event-stream"
                    ):
                        raise _ProtocolError("expected text/event-stream")
                    started = _clock.monotonic()
                    healthy = True
                    decoder = _SseDecoder()
                    async with aclosing(stream.iter_bytes()) as chunks:
                        async for chunk in chunks:
                            for event, ident, data in decoder.feed(chunk):
                                remaining()
                                previous = state.cursor
                                result = await state.accept(event, ident, data)
                                remaining()
                                if state.cursor != previous or _clock.monotonic() - started >= 10:
                                    failures = 0
                                if result is not None:
                                    return result
                    raise _Retry("unexpected EOF before build log done")
                except (_Retry, ConnectionError, ClientTimeoutError) as exc:
                    remaining()
                    if healthy and _clock.monotonic() - started >= 10:
                        failures = 0
                    if failures >= 5:
                        raise BuildLogInterruptedError(
                            "build log reconnect budget exhausted",
                            cursor=state.cursor,
                        ) from exc
                    delay = (0.25, 0.5, 1, 2, 5)[failures] * random.uniform(0.8, 1.0)
                    failures += 1
                    if isinstance(exc, _Retry):
                        delay = max(delay, exc.retry_after)
                finally:
                    if stream is not None:
                        await stream.close()
            if attempt.cancelled_caught:
                raise timed_out()
            budget = remaining()
            await _sleep(min(delay, budget) if budget is not None else delay)
    except BuildLogInterruptedError:
        raise
    except KeyboardInterrupt as exc:
        raise BuildLogInterruptedError(
            "build log observation canceled",
            cursor=observer.cursor if observer else cursor,
            code="BUILD_LOG_CANCELED",
        ) from exc
    except asyncio.CancelledError as exc:
        # Cancellation stays cancellation (task groups, timeouts and cancel
        # scopes rely on it). The checkpoint rides along; the sync surface
        # reports it as BUILD_LOG_CANCELED once the interrupted call unwinds.
        _attach_interrupt_error(
            exc,
            BuildLogInterruptedError(
                "build log observation canceled",
                cursor=observer.cursor if observer else cursor,
                code="BUILD_LOG_CANCELED",
            ),
        )
        raise
    except Exception as exc:
        checkpoint = observer.cursor if observer else cursor
        if isinstance(exc, _CallbackError):
            code = "BUILD_LOG_CALLBACK_ERROR"
        elif isinstance(exc, _ProtocolError):
            code = "BUILD_LOG_PROTOCOL_ERROR"
        else:
            code = exc.code if isinstance(exc, DaytonaError) and exc.code else "BUILD_LOG_INTERRUPTED"
        raise BuildLogInterruptedError(
            str(exc),
            cursor=checkpoint,
            code=code,
            status_code=exc.status_code if isinstance(exc, DaytonaError) else None,
        ) from exc
