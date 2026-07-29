# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict, cast

from ..common.errors import (
    SOURCE_DAEMON,
    DaytonaDaemonUpgradeRequiredError,
    DaytonaError,
    DaytonaProcessCursorExpiredError,
    DaytonaValidationError,
    create_daytona_error,
)
from ..common.process import ProcessHandleJSON
from ..common.pty import PtyResult

PROCESS_NOT_FOUND_CODE = "PROCESS_NOT_FOUND"
CURSOR_EXPIRED_CODE = "CURSOR_EXPIRED"
DAEMON_UPGRADE_REQUIRED_CODE = "DAEMON_UPGRADE_REQUIRED"
DAEMON_UPGRADE_REQUIRED_MESSAGE = "Process v2 requires a newer Daytona daemon. Upgrade the sandbox daemon and retry."


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
class ParsedProcessV2Error:
    message: str
    code: str | None
    source: str | None
    first_available_cursor: str | None


def parse_process_v2_error(body: str | None, fallback_message: str) -> ParsedProcessV2Error:
    if body is None:
        return ParsedProcessV2Error(
            message=fallback_message,
            code=None,
            source=None,
            first_available_cursor=None,
        )

    message = body
    code: str | None = None
    source: str | None = None
    first_available_cursor: str | None = None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ParsedProcessV2Error(
            message=fallback_message if fallback_message else body,
            code=None,
            source=None,
            first_available_cursor=None,
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
        # The recovery cursor rides in the shared envelope's "details" map.
        details_value: object | None = typed_payload.get("details")
        if isinstance(details_value, dict):
            typed_details: dict[str, object] = cast(dict[str, object], details_value)
            cursor_value: object | None = typed_details.get("firstAvailableCursor")
            if isinstance(cursor_value, str):
                first_available_cursor = cursor_value

    return ParsedProcessV2Error(
        message=message,
        code=code,
        source=source,
        first_available_cursor=first_available_cursor,
    )


def process_v2_error_from_response(
    *,
    status_code: int | None,
    headers: Mapping[str, str] | None,
    body: str | None,
    fallback_message: str,
) -> DaytonaError:
    parsed = parse_process_v2_error(body, fallback_message)
    if status_code == 404 and not _is_supported_daemon_error(parsed.source, parsed.code):
        return DaytonaDaemonUpgradeRequiredError(
            DAEMON_UPGRADE_REQUIRED_MESSAGE,
            status_code=status_code,
            headers=headers,
            code=DAEMON_UPGRADE_REQUIRED_CODE,
            source=SOURCE_DAEMON,
        )

    if parsed.code == CURSOR_EXPIRED_CODE:
        return DaytonaProcessCursorExpiredError(
            parsed.message,
            status_code=status_code,
            headers=headers,
            code=parsed.code,
            source=parsed.source,
            first_available_cursor=parsed.first_available_cursor,
        )

    return create_daytona_error(
        parsed.message,
        status_code=status_code,
        headers=headers,
        code=parsed.code,
        source=parsed.source,
    )


def should_mark_process_v2_supported(status_code: int | None, source: str | None, code: str | None) -> bool:
    if status_code is not None and status_code < 400:
        return True
    return _is_supported_daemon_error(source, code)


def decode_process_handle_json(data: ProcessHandleJSON | Mapping[str, str]) -> tuple[str, str]:
    sandbox_id = data.get("sandboxId")
    process_id = data.get("processId")
    if sandbox_id is None or sandbox_id == "":
        raise DaytonaValidationError("Process handle JSON requires a non-empty sandboxId")
    if process_id is None or process_id == "":
        raise DaytonaValidationError("Process handle JSON requires a non-empty processId")
    return sandbox_id, process_id


def encode_process_handle_json(*, sandbox_id: str, process_id: str) -> ProcessHandleJSON:
    return {"sandboxId": sandbox_id, "processId": process_id}


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


def _is_supported_daemon_error(source: str | None, code: str | None) -> bool:
    return source == SOURCE_DAEMON and code is not None and code != DAEMON_UPGRADE_REQUIRED_CODE
