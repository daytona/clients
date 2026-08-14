# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict, cast

from ..common.errors import DaytonaError, DaytonaProcessCursorExpiredError, DaytonaValidationError, create_daytona_error
from ..common.pty import PtyResult

PROCESS_NOT_FOUND_CODE = "PROCESS_NOT_FOUND"
CURSOR_EXPIRED_CODE = "CURSOR_EXPIRED"


class ProcessTerminalPayload(TypedDict, total=False):
    cols: int
    rows: int
    term: str


class CreateProcessPayload(TypedDict, total=False):
    argv: list[str]
    shell_command: str
    shell: str
    login: bool
    name: str
    session_id: str
    cwd: str
    env: dict[str, str]
    user: str
    stdin: str
    timeout_ms: int
    kind: str
    terminal: ProcessTerminalPayload
    keep_logs: str


class KillProcessPayload(TypedDict, total=False):
    signal: str
    escalate_after_ms: int
    escalate_to: str


class ProcessStdinPayload(TypedDict, total=False):
    data: str
    eof: bool


class ResizeProcessPayload(TypedDict):
    cols: int
    rows: int


@dataclass(frozen=True, slots=True)
class ParsedProcessError:
    message: str
    code: str | None
    source: str | None


def parse_process_error(body: str | None, fallback_message: str) -> ParsedProcessError:
    if body is None:
        return ParsedProcessError(
            message=fallback_message,
            code=None,
            source=None,
        )

    message = body
    code: str | None = None
    source: str | None = None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ParsedProcessError(
            message=fallback_message if fallback_message else body,
            code=None,
            source=None,
        )

    if isinstance(payload, dict):
        typed_payload: dict[str, object] = cast(dict[str, object], payload)
        message_value: object | None = typed_payload.get("message")
        if isinstance(message_value, str):
            message = message_value
        code_value: object | None = typed_payload.get("code")
        if isinstance(code_value, str):
            code = code_value
        source_value: object | None = typed_payload.get("source")
        if isinstance(source_value, str):
            source = source_value
    return ParsedProcessError(
        message=message,
        code=code,
        source=source,
    )


def parse_json_object(data: str, error_message: str) -> dict[str, object]:
    """Parse a JSON document that must be an object, typed for strict checking."""
    payload: object = json.loads(data)
    if not isinstance(payload, dict):
        raise DaytonaError(error_message)
    return cast(dict[str, object], payload)


def process_error_from_response(
    *,
    status_code: int | None,
    headers: Mapping[str, str] | None,
    body: str | None,
    fallback_message: str,
) -> DaytonaError:
    parsed = parse_process_error(body, fallback_message)
    if parsed.code == CURSOR_EXPIRED_CODE:
        return DaytonaProcessCursorExpiredError(
            parsed.message,
            status_code=status_code,
            headers=headers,
            code=parsed.code,
            source=parsed.source,
        )

    return create_daytona_error(
        parsed.message,
        status_code=status_code,
        headers=headers,
        code=parsed.code,
        source=parsed.source,
    )


def normalize_process_stdin(data: str | bytes) -> str:
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DaytonaValidationError("Process stdin bytes must be valid UTF-8") from exc


def pty_result_from_process_result(
    *,
    exit_code: int | None,
    reason: str,
    signal: str | None,
) -> PtyResult:
    match reason:
        case "exited":
            return PtyResult(exit_code=exit_code, error=None)
        case "signaled":
            return PtyResult(exit_code=exit_code, error=signal)
        case "timed_out":
            return PtyResult(exit_code=exit_code, error="timed_out")
        case "sandbox_stopped":
            return PtyResult(exit_code=exit_code, error="sandbox_stopped")
        case "failed":
            return PtyResult(exit_code=exit_code, error="failed")
        case _:
            return PtyResult(exit_code=exit_code, error=reason)
