# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import (
    AsyncIterable,
    AsyncIterator,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeGuard,
    TypeVar,
    Union,
    get_args,
)
from urllib.parse import quote, unquote

from pydantic import ValidationError

from ._gen.api import LifecyclePolicy
from ._gen.toolbox import (
    BranchList,
    CommitResponse,
    ExitResult,
    FileInfo,
    IsPortInUseResponse,
    LogFrameDto,
    PortList,
    ProcessDto,
    ProcessListResponse,
    ProcessLogsResponse,
    SearchResponse,
    SessionDto,
    SessionListResponse,
    StatusResponse,
)
from ._gen.toolbox import (
    DockerStatus as DockerStatusModel,
)
from ._gen.toolbox import (
    TerminalSize as ToolboxTerminalSize,
)
from .errors import DaytonaError, UnsupportedOperationError, create_error
from .types import (
    DISABLED,
    ArtifactRetention,
    BulkReadError,
    BulkReadResult,
    BulkWriteFile,
    BulkWriteResult,
    DockerfileImageSource,
    DockerStatus,
    Image,
    ImageRef,
    ImageSource,
    JsonObject,
    JsonValue,
    KeepLogs,
    ListResult,
    LogFrame,
    LogPage,
    ManifestImageSource,
    NamedImageSource,
    ProcessInfo,
    ProcessKind,
    ProcessStateFilter,
    RegistryImageSource,
    SandboxLifecycle,
    SandboxState,
    SearchSandboxesOptions,
    SearchTimestamp,
    SearchTotalRelation,
    Snapshot,
    SnapshotKind,
    SnapshotRef,
    StdinMode,
    TerminalSize,
    WaitResult,
    _docker_owner_or_unknown,
    _docker_state_or_unknown,
    _process_info_kind_or_unknown,
    _process_keep_logs_or_unknown,
    _process_state_or_unknown,
    _process_stdin_mode_or_unknown,
    _wait_reason_or_unknown,
)

TransportPayload = Union[JsonValue, bytes]
ProcessPagePayload = Union[TransportPayload, List[ProcessDto], ProcessListResponse]
ProcessLogPayload = Union[TransportPayload, List[LogFrameDto], ProcessLogsResponse]
WaitPayload = ExitResult | ProcessDto
MultipartPart = Tuple[Optional[str], Union[str, bytes]]
DAYTONA_FILE_BOUNDARY = "DAYTONA-FILE-BOUNDARY"
_MULTIPART_BOUNDARY_RE = re.compile(r'\bboundary\s*=\s*(?:"([^"]+)"|([^;]+))', re.IGNORECASE)
_CONTENT_DISPOSITION_NAME_RE = re.compile(r'name="([^"]+)"', re.IGNORECASE)
_CONTENT_DISPOSITION_FILENAME_RE = re.compile(r'filename="([^"]*)"', re.IGNORECASE)
_CONTENT_DISPOSITION_FILENAME_STAR_RE = re.compile(r"filename\*=utf-8''([^;]+)", re.IGNORECASE)
ModelT = TypeVar("ModelT")


@dataclass(frozen=True)
class RequestSpec:
    method: str
    path: str
    params: Dict[str, JsonValue] = field(default_factory=dict)
    json_body: Optional[JsonValue] = None
    data: Optional[Union[str, bytes, Dict[str, str]]] = None
    files: Optional[Dict[str, MultipartPart]] = None
    timeout: Optional[float] = None
    unbounded_read: bool = False
    # A file transfer (upload, download, the bulk byte calls): never bounded by
    # the client's request timeout. It runs until it finishes or the caller
    # cancels it.
    transfer: bool = False
    headers: Dict[str, str] = field(default_factory=dict)
    # Called with the response headers of a successful request (status < 400)
    # before the body is decoded. The events hub learns a sandbox's service
    # endpoint from X-Daytona-Cell on mutation responses this way; a
    # transport that cannot see headers simply never calls it.
    # Not part of the request's identity: two specs that send the same request
    # are equal whatever they do with the response headers.
    on_headers: Optional[Callable[[Mapping[str, str]], None]] = field(default=None, compare=False, repr=False)


def parse_error_payload(status_code: int, payload: JsonObject) -> DaytonaError:
    # ``error`` is a message fallback for bodies without a standard envelope.
    # It never becomes a ``code``: dispatch stays on ``code``/``source``/status.
    message = (
        _optional_str(payload.get("message")) or _optional_str(payload.get("error")) or f"HTTP {status_code}"
    )
    retry_after_ms = payload.get("retryAfterMs")
    return create_error(
        message,
        status_code,
        _optional_str(payload.get("code")),
        _optional_str(payload.get("source")),
        trace_id=_optional_str(payload.get("trace_id")),
        retry_after_seconds=_optional_int(payload.get("retry_after_seconds")),
        feature=_optional_str(payload.get("feature")),
        entitlement=_optional_str(payload.get("entitlement")),
        replacement=_optional_str(payload.get("replacement")),
        first_available_cursor=_optional_str(payload.get("firstAvailableCursor")),
        limit=_optional_str(payload.get("limit")),
        current=_optional_int(payload.get("current")),
        max=_optional_int(payload.get("max")),
        max_bytes=_optional_int(payload.get("max_bytes")),
        bytes=_optional_int(payload.get("bytes")),
        stated=_optional_str(payload.get("stated")),
        actual=_optional_str(payload.get("actual")),
        region=_optional_str(payload.get("region")),
        request_id=_optional_str(payload.get("requestId")),
        retry_after_ms=(
            retry_after_ms
            if isinstance(retry_after_ms, int)
            and not isinstance(retry_after_ms, bool)
            and retry_after_ms >= 0
            else None
        ),
    )


def parse_json_bytes(content: bytes) -> JsonValue:
    if not content:
        return {}
    value = json.loads(content.decode("utf-8"))
    return value


def error_from_response(status_code: int, body: bytes) -> DaytonaError:
    try:
        payload = expect_object(parse_json_bytes(body))
    except Exception:
        payload = {"message": body.decode("utf-8", errors="replace") or f"HTTP {status_code}"}
    return parse_error_payload(status_code, payload)


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and is_json_value(item) for key, item in value.items())
    return False


def json_object_from_mapping(payload: Mapping[str, object]) -> JsonObject:
    """Widen a contract TypedDict back into the SDK's public JsonObject shape."""
    return {key: value for key, value in payload.items() if is_json_value(value)}


def expect_object(value: TransportPayload) -> JsonObject:
    if isinstance(value, dict):
        return value
    raise create_error("expected JSON object", 400, "BAD_REQUEST", "SDK")


def enum_str(member: Enum) -> str:
    """The wire string of a generated enum member, including the pseudo-member
    an unknown (newer-server) value becomes — typed as str, not Any."""
    return str(member.value)


def _first_validation_error_line(error: ValidationError) -> str:
    details = error.errors(include_url=False)
    if not details:
        return str(error).splitlines()[0]
    first = details[0]
    path = ".".join(str(part) for part in first["loc"])
    message = first["msg"]
    return f"{path}: {message}" if path else message


def _missing_required_key(value: JsonValue, model_type: object, path: str) -> Optional[str]:
    """The first contract-required key absent from ``value`` (nested models and
    list items included), or ``None``.

    The generated ``from_dict`` reads a required key with ``obj.get``, so an
    absent required-but-nullable field would pass as ``null``: the contract
    allows an explicit ``null``, not a missing key.
    """
    if isinstance(value, list):
        for index, item in enumerate(value):
            missing = _missing_required_key(item, model_type, f"{path}[{index}]")
            if missing is not None:
                return missing
        return None
    model_fields = getattr(model_type, "model_fields", None)
    if not isinstance(value, dict) or not isinstance(model_fields, dict):
        return None
    for name, info in model_fields.items():
        key = info.alias or name
        if info.is_required() and key not in value:
            return f"{path}{key}"
        child = value.get(key)
        if child is None:
            continue
        for candidate in _model_types(info.annotation):
            missing = _missing_required_key(child, candidate, f"{path}{key}.")
            if missing is not None:
                return missing
    return None


def _model_types(annotation: object) -> List[object]:
    """The generated API models an annotation names (through Optional / List)."""
    module = getattr(annotation, "__module__", "")
    if isinstance(annotation, type) and module.startswith("daytona.v2._gen.api"):
        return [annotation]
    found: List[object] = []
    for argument in get_args(annotation):
        found.extend(_model_types(argument))
    return found


def _expect_model_from_dict(
    value: TransportPayload | ModelT,
    model_type: type[ModelT],
    description: str,
    from_dict: Callable[[JsonObject], ModelT | None],
) -> ModelT:
    if isinstance(value, model_type):
        return value
    if not isinstance(value, dict):
        raise create_error(f"expected {description}", 400, "BAD_REQUEST", "SDK")
    if model_type.__module__.startswith("daytona.v2._gen.api"):
        missing = _missing_required_key(value, model_type, "")
        if missing is not None:
            # A list item's path keeps its index; a nested key reads `a.b`.
            raise create_error(f"expected {description}: {missing}: Field required", 400, "BAD_REQUEST", "SDK")
    try:
        parsed = from_dict(value)
    except ValidationError as exc:
        raise create_error(
            f"expected {description}: {_first_validation_error_line(exc)}",
            400,
            "BAD_REQUEST",
            "SDK",
        ) from exc
    if parsed is None:
        raise create_error(f"expected {description}", 400, "BAD_REQUEST", "SDK")
    return parsed


LIFECYCLE_DURATION_FIELDS: Tuple[str, ...] = ("timeout_seconds", "idle_expiry_seconds")
LIFECYCLE_ACTION_FIELDS: Tuple[str, ...] = ("on_timeout", "on_idle_timeout")
LIFECYCLE_FIELDS: Tuple[str, ...] = (
    "timeout_seconds",
    "on_timeout",
    "idle_expiry_seconds",
    "on_idle_timeout",
)
LIFECYCLE_ACTIONS: Tuple[str, ...] = ("stop", "pause", "delete")
# Lifecycle durations cannot exceed 2147483647 seconds. Validate here so the
# client rejects an invalid duration before submitting the request. The org/platform
# maximum on timeout_seconds is deliberately NOT mirrored: it is per-org state the
# client cannot see.
LIFECYCLE_MAX_SECS: int = 2147483647


def lifecycle_body(lifecycle: Optional[JsonValue], *, creating: bool = False) -> Optional[JsonObject]:
    """Validate a lifecycle policy and render its wire object, or None when empty.

    ``creating`` requires an idle timeout and its action together:
    ``idle_expiry_seconds`` and ``on_idle_timeout`` must arrive together. On a
    PATCH either may change one half of an already-armed pair, so that rule is
    left to the server, which sees the merged state.
    """
    if lifecycle is None:
        return None
    if not isinstance(lifecycle, dict):
        raise create_error("lifecycle_policy must be an object", 400, "BAD_REQUEST", "SDK")
    # The API rejects unrecognized lifecycle fields with a 400.
    # The wire object below is projected
    # field-by-field from the known set, which would instead DISCARD a typo
    # silently — and when the typo was the only key, `lifecycle` is dropped
    # whole and the sandbox is created with no policy at all. Reject first, so a
    # misspelled knob can never read as success.
    unknown = [str(name) for name in lifecycle if name not in LIFECYCLE_FIELDS]
    if unknown:
        raise create_error(
            f"lifecycle_policy contains unknown fields: {', '.join(unknown)}", 400, "BAD_REQUEST", "SDK"
        )
    body: JsonObject = {}
    for name in LIFECYCLE_DURATION_FIELDS:
        value = lifecycle.get(name)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise create_error(
                f"lifecycle_policy.{name} must be an integer number of seconds", 400, "BAD_REQUEST", "SDK"
            )
        if name == "timeout_seconds" and value < 1:
            raise create_error(
                "lifecycle_policy.timeout_seconds must be at least 1; the timeout cannot be disabled",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        if name == "idle_expiry_seconds" and value == 0:
            raise create_error(
                f"lifecycle_policy.idle_expiry_seconds: 0 is not a valid budget; use {DISABLED} to disable",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        if value < DISABLED:
            raise create_error(
                f"lifecycle_policy.{name} must be >= {DISABLED}: use {DISABLED} to disable",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        if value > LIFECYCLE_MAX_SECS:
            raise create_error(
                f"lifecycle_policy.{name} must not exceed {LIFECYCLE_MAX_SECS} seconds",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        body[name] = value
    for name in LIFECYCLE_ACTION_FIELDS:
        action = lifecycle.get(name)
        if action is None:
            continue
        # `str`, not an exact-type check: the generated `LifecycleAction` enum
        # is a `str` subclass, and its `.value` is the wire string.
        wire = action.value if isinstance(action, Enum) else action
        if not isinstance(wire, str) or wire not in LIFECYCLE_ACTIONS:
            raise create_error(
                f"lifecycle_policy.{name} must be one of: {', '.join(LIFECYCLE_ACTIONS)}",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        body[name] = wire
    # Disabling the idle timeout and specifying its action are contradictory.
    if body.get("idle_expiry_seconds") == DISABLED and "on_idle_timeout" in body:
        raise create_error(
            "lifecycle_policy.on_idle_timeout requires an idle timeout: it cannot be set while "
            f"clearing idle_expiry_seconds with {DISABLED}",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    if creating:
        armed_idle = _lifecycle_enabled(body, "idle_expiry_seconds")
        if armed_idle and "on_idle_timeout" not in body:
            raise create_error(
                "lifecycle_policy.idle_expiry_seconds requires lifecycle_policy.on_idle_timeout "
                f"({' | '.join(LIFECYCLE_ACTIONS)}); the idle action has no default",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        if not armed_idle and "on_idle_timeout" in body:
            raise create_error(
                "lifecycle_policy.on_idle_timeout requires an idle timeout: supply lifecycle_policy.idle_expiry_seconds",
                400,
                "BAD_REQUEST",
                "SDK",
            )
    return body or None


def assert_lifecycle_class_support(
    lifecycle: Optional[JsonObject],
    *,
    sandbox_class: str,
    persistent: bool,
    action: str = "create",
) -> None:
    """Reject lifecycle settings unavailable for this sandbox's class and persistence."""
    if not lifecycle:
        return
    offending: list[str] = []
    for name in LIFECYCLE_ACTION_FIELDS:
        value = lifecycle.get(name)
        if isinstance(value, str) and not _lifecycle_action_allowed(value, sandbox_class, persistent):
            offending.append(f"lifecycle_policy.{name}={value}")
    if not offending:
        return
    raise create_error(
        f"{sandbox_class} {action} does not accept: {', '.join(offending)}", 400, "BAD_REQUEST", "SDK"
    )


def _lifecycle_action_allowed(action: str, sandbox_class: str, persistent: bool) -> bool:
    """Available lifecycle actions for Linux and macOS sandboxes."""
    match action:
        case "delete":
            return True
        case "stop":
            return persistent and sandbox_class == "macos"
        case "pause":
            return False
        case _:
            # Unknown values never reach here — lifecycle_body rejects them.
            return True


def parse_lifecycle(payload: JsonObject | LifecyclePolicy) -> SandboxLifecycle:
    """Parse a lifecycle object, accepting both bare and sandbox-nested shapes."""
    if isinstance(payload, LifecyclePolicy):
        model = payload
        return SandboxLifecycle(
            timeout_seconds=model.timeout_seconds,
            on_timeout=model.on_timeout.value if model.on_timeout is not None else None,
            timeout_at=parse_timestamp(model.timeout_at),
            idle_expiry_seconds=model.idle_expiry_seconds,
            on_idle_timeout=model.on_idle_timeout.value if model.on_idle_timeout is not None else None,
            idle_timeout_at=parse_timestamp(model.idle_timeout_at),
            last_activity_at=parse_timestamp(model.last_activity_at),
            last_activity_source=model.last_activity_source,
        )
    raw_payload = payload
    nested = raw_payload.get("lifecycle_policy")
    data: JsonObject = nested if isinstance(nested, dict) else raw_payload
    return SandboxLifecycle(
        timeout_seconds=_optional_int(data.get("timeout_seconds")),
        on_timeout=_optional_str(data.get("on_timeout")),
        timeout_at=parse_timestamp(data.get("timeout_at")),
        idle_expiry_seconds=_optional_int(data.get("idle_expiry_seconds")),
        on_idle_timeout=_optional_str(data.get("on_idle_timeout")),
        idle_timeout_at=parse_timestamp(data.get("idle_timeout_at")),
        last_activity_at=parse_timestamp(data.get("last_activity_at")),
        last_activity_source=_optional_str(data.get("last_activity_source")),
    )


def parse_timestamp(value: object) -> Optional[datetime]:
    """A wire timestamp as a timezone-aware ``datetime``; ``None`` when absent or unparseable.

    Accepts the RFC 3339 strings of a raw body and the ``datetime`` values of a
    validated wire model. A value without an offset is taken as UTC.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        # Python 3.10's fromisoformat takes at most six fraction digits.
        match = re.match(r"^(.*T\d{2}:\d{2}:\d{2})\.(\d+)(.*)$", text)
        if match is not None:
            text = f"{match.group(1)}.{match.group(2)[:6].ljust(6, '0')}{match.group(3)}"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def rfc3339(value: datetime) -> str:
    """The RFC 3339 (UTC, ``Z``) rendering of a timestamp, as both SDKs serialise it."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _lifecycle_enabled(body: JsonObject, name: str) -> bool:
    return name in body and body[name] != DISABLED


def _optional_int(value: JsonValue) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: JsonValue) -> Optional[str]:
    # A generated str-enum member (from a model's to_dict()) is its plain value.
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) else None


def expect_branch_list(value: TransportPayload) -> BranchList:
    return _expect_model_from_dict(value, BranchList, "branch list response", BranchList.from_dict)


def expect_commit_response(value: TransportPayload) -> CommitResponse:
    return _expect_model_from_dict(value, CommitResponse, "commit response", CommitResponse.from_dict)


def expect_exit_result(value: TransportPayload) -> ExitResult:
    return _expect_model_from_dict(value, ExitResult, "wait result", ExitResult.from_dict)


def expect_file_info(value: TransportPayload, *, parent: Optional[str] = None) -> FileInfo:
    return _expect_model_from_dict(
        value, FileInfo, "file info response", lambda obj: _file_info_from_wire(obj, parent=parent)
    )


# Optional file metadata is represented by the empty string when unavailable,
# never by a value
# in another field's representation (``mode`` is symbolic, ``permissions``
# octal; ``modTime`` and ``modifiedAt`` differ in format).
_OPTIONAL_FILE_INFO_FIELDS = ("modifiedAt", "owner", "group", "permissions")


def _file_info_from_wire(obj: JsonObject, *, parent: Optional[str]) -> FileInfo | None:
    """Parse file information, allowing optional metadata to be unavailable.

    ``path`` is the one field that CAN be derived faithfully: a listing knows
    the directory it asked for, so ``parent/name`` is the entry's real
    location. Without a parent (``stat``), a missing path stays empty.
    """
    filled = dict(obj)
    if not filled.get("path"):
        name = filled.get("name")
        filled["path"] = f"{parent.rstrip('/')}/{name}" if parent and isinstance(name, str) and name else ""
    for key in _OPTIONAL_FILE_INFO_FIELDS:
        if filled.get(key) is None:
            filled[key] = ""
    return FileInfo.from_dict(filled)


def expect_is_port_in_use_response(value: TransportPayload) -> IsPortInUseResponse:
    return _expect_model_from_dict(
        value,
        IsPortInUseResponse,
        "port-in-use response",
        IsPortInUseResponse.from_dict,
    )


def expect_log_frame_dto(value: TransportPayload | LogFrameDto) -> LogFrameDto:
    return _expect_model_from_dict(value, LogFrameDto, "log frame", LogFrameDto.from_dict)


def expect_port_list(value: TransportPayload) -> PortList:
    return _expect_model_from_dict(value, PortList, "port list response", PortList.from_dict)


def expect_process_dto(value: TransportPayload | ProcessDto) -> ProcessDto:
    return _expect_model_from_dict(value, ProcessDto, "process response", ProcessDto.from_dict)


def expect_process_log_page(value: TransportPayload) -> ProcessLogsResponse:
    return _expect_model_from_dict(
        value,
        ProcessLogsResponse,
        "process log page",
        ProcessLogsResponse.from_dict,
    )


def expect_process_list_response(value: TransportPayload) -> ProcessListResponse:
    return _expect_model_from_dict(
        value,
        ProcessListResponse,
        "process list response",
        ProcessListResponse.from_dict,
    )


def expect_search_response(value: TransportPayload) -> SearchResponse:
    return _expect_model_from_dict(value, SearchResponse, "search response", SearchResponse.from_dict)


def expect_session_dto(value: TransportPayload) -> SessionDto:
    return _expect_model_from_dict(value, SessionDto, "session response", SessionDto.from_dict)


def expect_session_list_response(value: TransportPayload) -> SessionListResponse:
    return _expect_model_from_dict(
        value,
        SessionListResponse,
        "session list response",
        SessionListResponse.from_dict,
    )


def expect_docker_status(value: TransportPayload) -> DockerStatus:
    return parse_docker_status(
        _expect_model_from_dict(value, DockerStatusModel, "docker status", DockerStatusModel.from_dict)
    )


def expect_status_response(value: TransportPayload) -> StatusResponse:
    return _expect_model_from_dict(value, StatusResponse, "git status response", StatusResponse.from_dict)


# Slack added to a bounded long-poll read (/processes?waitMs, /processes/{id}/wait) so the server's long-poll expiry
# (timeoutMs) normally answers first and a stalled request still cannot hold
# the caller past its budget by more than this.
WAIT_READ_SLACK_SECONDS = 5.0


def bounded_request_timeout(timeout: Optional[float]) -> Optional[float]:
    """Per-request HTTP timeout for a caller budget.

    ``None`` (omitted) keeps the transport default; ``0`` means unbounded and
    must be paired with ``unbounded_read=True`` (a literal ``0`` would otherwise
    be sent as a zero-second deadline); a positive budget is used as is.
    """
    return None if timeout is None or timeout == 0 else timeout


def process_create_spec(
    command: Optional[str],
    *,
    argv: Optional[Sequence[str]],
    code: Optional[str],
    language: Optional[str],
    kind: ProcessKind,
    name: Optional[str],
    session_id: Optional[str],
    cwd: Optional[str],
    env: Optional[Mapping[str, str]],
    user: Optional[str],
    shell: Optional[str],
    login: Optional[bool],
    stdin: StdinMode,
    execution_timeout: Optional[float],
    terminal: Optional[TerminalSize],
    keep_logs: Optional[KeepLogs],
    wait_ms: Optional[int] = None,
    inline_logs: bool = False,
    request_timeout: Optional[float] = None,
) -> RequestSpec:
    chosen = [command is not None, argv is not None, code is not None]
    if kind in ("pty", "session") and sum(1 for item in chosen if item) == 0:
        pass
    elif sum(1 for item in chosen if item) != 1:
        raise create_error("provide exactly one of command, argv, or code", 400, "BAD_REQUEST", "SDK")
    if code is not None and user is not None:
        raise UnsupportedOperationError(
            "user is not supported for code processes; the kernel runs as the sandbox user",
            status_code=400,
            code="UNSUPPORTED_OPERATION",
            source="SDK",
        )
    if terminal is not None and kind != "pty":
        raise create_error(
            "terminal cols/rows are only supported for pty processes; use sandbox.pty.start()",
            400,
            "UNSUPPORTED_OPERATION",
            "SDK",
        )
    execution_timeout_ms = None
    if execution_timeout is not None:
        execution_timeout_ms = timeout_millis_from_seconds(execution_timeout)
    body: JsonObject = {"kind": kind}
    if command is not None:
        body["shellCommand"] = command
    if argv is not None:
        body["argv"] = list(argv)
    if code is not None:
        body["code"] = code
    optional: Dict[str, JsonValue] = {
        "language": language,
        "name": name,
        "sessionId": session_id,
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
        "user": user,
        "shell": shell,
        "login": login,
        # Sessions do not accept this process-input parameter.
        "stdin": None if kind == "session" else stdin,
        "timeoutMs": None if kind == "code" else execution_timeout_ms,
        "keepLogs": keep_logs,
    }
    if terminal is not None:
        optional["terminal"] = {"cols": terminal.cols, "rows": terminal.rows}
    body.update({key: value for key, value in optional.items() if value is not None})
    if wait_ms is None:
        return RequestSpec(
            "POST",
            "/processes",
            json_body=body,
            timeout=bounded_request_timeout(request_timeout),
            unbounded_read=request_timeout == 0,
        )
    # waitMs holds the request for the caller budget. The HTTP
    # read must outlive that hold (budget + slack) so the server's expiry
    # answers first — but a positive budget still bounds it, so a stalled
    # request cannot hold the caller indefinitely. waitMs=0 = unbounded read.
    params: Dict[str, JsonValue] = {"waitMs": wait_ms}
    if inline_logs:
        params["logs"] = "text"
    bounded = None if wait_ms == 0 else wait_ms / 1000 + WAIT_READ_SLACK_SECONDS
    return RequestSpec(
        "POST",
        "/processes",
        params=params,
        json_body=body,
        timeout=bounded,
        unbounded_read=bounded is None,
    )


def parse_process(payload: ProcessDto) -> ProcessInfo:
    raw_kind = enum_str(payload.kind)
    raw_state = enum_str(payload.state)
    raw_stdin = enum_str(payload.stdin)
    raw_keep_logs = enum_str(payload.keep_logs)
    raw_reason = enum_str(payload.reason) if payload.reason is not None else None
    return ProcessInfo(
        id=payload.id,
        name=payload.name,
        pid=payload.pid,
        kind=_process_info_kind_or_unknown(raw_kind),
        state=_process_state_or_unknown(raw_state),
        system=payload.system is True,
        session_id=payload.session_id,
        shell_command=payload.shell_command,
        argv=payload.argv,
        code=payload.code,
        language=payload.language,
        shell=payload.shell,
        resolved_shell=payload.resolved_shell,
        login=payload.login,
        cwd=payload.cwd,
        user=payload.user,
        stdin=_process_stdin_mode_or_unknown(raw_stdin),
        execution_timeout=timeout_seconds_from_millis(payload.timeout_ms),
        terminal=_parse_terminal(payload.terminal),
        keep_logs=_process_keep_logs_or_unknown(raw_keep_logs),
        exit_code=payload.exit_code,
        signal=payload.signal,
        reason=_wait_reason_or_unknown(raw_reason) if raw_reason is not None else None,
        created_at=payload.created_at,
        started_at=payload.started_at,
        exited_at=payload.exited_at,
        truncated_head=payload.truncated_head,
        first_available_cursor=payload.first_available_cursor,
        last_cursor=payload.last_cursor,
        raw_kind=raw_kind,
        raw_state=raw_state,
        raw_stdin=raw_stdin,
        raw_keep_logs=raw_keep_logs,
        raw_reason=raw_reason,
    )


def process_list_spec(
    *,
    state: Optional[ProcessStateFilter],
    kind: Optional[ProcessKind],
    session_id: Optional[str],
    name: Optional[str],
    pid: Optional[int],
) -> RequestSpec:
    params: Dict[str, JsonValue] = {}
    for key, value in {
        "state": state,
        "kind": kind,
        "sessionId": session_id,
        "name": name,
        "pid": pid,
    }.items():
        if value is not None:
            params[key] = value
    return RequestSpec("GET", "/processes", params=params)


def parse_process_list(payload: ProcessPagePayload) -> List[ProcessInfo]:
    if isinstance(payload, ProcessListResponse):
        return [parse_process(item) for item in payload.items]
    if isinstance(payload, list):
        if all(isinstance(item, ProcessDto) for item in payload):
            return [parse_process(item) for item in payload]
        return [parse_process(expect_process_dto(item)) for item in payload]
    if isinstance(payload, dict):
        obj = payload
    else:
        obj = expect_object(payload)
    raw_items = obj.get("items") or obj.get("processes") or []
    if not isinstance(raw_items, list):
        return []
    return [parse_process(expect_process_dto(item)) for item in raw_items]


def parse_process_page(payload: ProcessPagePayload) -> ListResult[ProcessInfo]:
    if isinstance(payload, ProcessListResponse):
        return ListResult(items=[parse_process(item) for item in payload.items], next_cursor=None)
    if isinstance(payload, list):
        if all(isinstance(item, ProcessDto) for item in payload):
            return ListResult(items=[parse_process(item) for item in payload], next_cursor=None)
        return ListResult(
            items=[parse_process(expect_process_dto(item)) for item in payload], next_cursor=None
        )
    if isinstance(payload, dict):
        obj = payload
    else:
        obj = expect_object(payload)
    raw_items = obj.get("items") or obj.get("processes") or []
    items = (
        [parse_process(expect_process_dto(item)) for item in raw_items] if isinstance(raw_items, list) else []
    )
    cursor = obj.get("nextCursor") or obj.get("next_cursor")
    return ListResult(items=items, next_cursor=str(cursor) if isinstance(cursor, str) else None)


def timeout_millis_from_seconds(timeout: Optional[float]) -> Optional[int]:
    if timeout is None:
        return None
    if not math.isfinite(timeout) or timeout < 0:
        raise create_error("timeout must be a non-negative finite number", 400, "BAD_REQUEST", "SDK")
    milliseconds = timeout * 1000
    if not math.isfinite(milliseconds) or milliseconds < 0:
        raise create_error("timeout must be a non-negative finite number", 400, "BAD_REQUEST", "SDK")
    return math.ceil(round(milliseconds, 6))


def timeout_seconds_from_millis(timeout_millis: JsonValue | None) -> Optional[float]:
    if isinstance(timeout_millis, (int, float)) and not isinstance(timeout_millis, bool):
        return timeout_millis / 1000
    return None


def wait_spec(process_id: str, *, timeout: Optional[float], unbounded: bool = False) -> RequestSpec:
    params: Dict[str, JsonValue] = {}
    timeout_millis = 0 if unbounded else timeout_millis_from_seconds(timeout)
    if timeout_millis is not None:
        params["timeoutMs"] = timeout_millis
    # A positive caller budget bounds the transport read too — a stalled
    # request must not block wait() past its documented budget. Unbounded
    # waits keep the unbounded read.
    bounded = None if unbounded or timeout is None or timeout == 0 else timeout + WAIT_READ_SLACK_SECONDS
    return RequestSpec(
        "GET",
        f"/processes/{quote(process_id, safe='')}/wait",
        params=params,
        timeout=bounded,
        unbounded_read=bounded is None,
    )


def parse_wait(payload: WaitPayload) -> WaitResult:
    signal = payload.signal
    if signal is not None and signal.startswith("SIG"):
        signal = signal[3:]
    raw_reason = enum_str(payload.reason) if payload.reason is not None else None
    return WaitResult(
        exit_code=payload.exit_code,
        signal=signal,
        reason=_wait_reason_or_unknown(raw_reason) if raw_reason is not None else None,
        raw_reason=raw_reason,
    )


def logs_spec(
    process_id: str,
    *,
    cursor: Optional[str],
    limit: Optional[int],
    encoding: str,
    follow: bool,
) -> RequestSpec:
    params: Dict[str, JsonValue] = {"encoding": encoding}
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = limit
    if follow:
        params["follow"] = "true"
    return RequestSpec("GET", f"/processes/{quote(process_id, safe='')}/logs", params=params, timeout=None)


def parse_log_page(payload: ProcessLogPayload) -> LogPage:
    if isinstance(payload, ProcessLogsResponse):
        frames = [_parse_log_frame(item) for item in payload.frames]
        return LogPage(
            items=frames,
            next_cursor=payload.next_cursor or (frames[-1].cursor if frames else None),
            truncated_head=payload.truncated_head,
            first_available_cursor=payload.first_available_cursor,
        )
    if isinstance(payload, list):
        if all(isinstance(item, LogFrameDto) for item in payload):
            frames = [_parse_log_frame(item) for item in payload]
        else:
            frames = [_parse_log_frame(expect_log_frame_dto(item)) for item in payload]
        return LogPage(items=frames, next_cursor=frames[-1].cursor if frames else None)
    if isinstance(payload, dict) and "frames" in payload:
        try:
            return parse_log_page(expect_process_log_page(payload))
        except DaytonaError:
            pass
    if isinstance(payload, dict):
        obj = payload
    else:
        obj = expect_object(payload)
    raw_items = obj.get("items") or obj.get("frames") or obj.get("logs") or []
    frames = (
        [_parse_log_frame(expect_log_frame_dto(item)) for item in raw_items]
        if isinstance(raw_items, list)
        else []
    )
    cursor_value = obj.get("nextCursor") or obj.get("next_cursor")
    next_cursor = cursor_value if isinstance(cursor_value, str) else None
    first_available_cursor = obj.get("firstAvailableCursor")
    return LogPage(
        items=frames,
        next_cursor=next_cursor or (frames[-1].cursor if frames else None),
        truncated_head=bool(obj.get("truncatedHead", False)),
        first_available_cursor=first_available_cursor if isinstance(first_available_cursor, str) else None,
    )


class SseParser:
    """Push-style SSE frame parser: feed lines, collect events.

    Sans-I/O so one parser serves both a sync and an async line source.
    """

    def __init__(self) -> None:
        self._event = "message"
        self._data_parts: List[str] = []

    def feed(self, raw: str) -> Optional[Tuple[str, JsonObject]]:
        line = raw.rstrip("\r\n")
        if not line:
            if not self._data_parts:
                self._event = "message"
                return None
            event = (self._event, expect_object(json.loads("\n".join(self._data_parts))))
            self._event = "message"
            self._data_parts = []
            return event
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._event = value
        elif name == "data":
            self._data_parts.append(value)
        return None


def parse_sse_lines(lines: Iterable[str]) -> Iterator[Tuple[str, JsonObject]]:
    parser = SseParser()
    for raw in lines:
        event = parser.feed(raw)
        if event is not None:
            yield event


async def parse_sse_lines_async(lines: AsyncIterable[str]) -> AsyncIterator[Tuple[str, JsonObject]]:
    parser = SseParser()
    async for raw in lines:
        event = parser.feed(raw)
        if event is not None:
            yield event


def stdin_spec(process_id: str, data: Union[str, bytes], *, eof: bool) -> RequestSpec:
    body: JsonObject = {"eof": eof}
    if isinstance(data, bytes):
        body["data"] = base64.b64encode(data).decode("ascii")
        body["encoding"] = "base64"
    else:
        body["data"] = data
    return RequestSpec("POST", f"/processes/{quote(process_id, safe='')}/stdin", json_body=body)


def signal_spec(
    process_id: str,
    *,
    signal: str,
    escalate_after_ms: Optional[int],
    escalate_to: str,
) -> RequestSpec:
    # The wire takes SIGTERM-style names; the SDK surface uses bare names
    # (TERM/INT/...) in both languages, so the prefix is added here.
    body: JsonObject = {"signal": f"SIG{signal}"}
    if escalate_after_ms is not None:
        body["escalateAfterMs"] = escalate_after_ms
        body["escalateTo"] = f"SIG{escalate_to}"
    return RequestSpec("POST", f"/processes/{quote(process_id, safe='')}/signals", json_body=body)


def resize_spec(process_id: str, *, cols: int, rows: int) -> RequestSpec:
    return RequestSpec(
        "POST", f"/processes/{quote(process_id, safe='')}/resize", json_body={"cols": cols, "rows": rows}
    )


def delete_process_spec(process_id: str) -> RequestSpec:
    return RequestSpec("DELETE", f"/processes/{quote(process_id, safe='')}")


def file_download_spec(path: str, *, timeout: Optional[float]) -> RequestSpec:
    return RequestSpec("GET", "/files/download", params={"path": path}, timeout=timeout, transfer=True)


def file_upload_spec(
    path: str, content: bytes, *, timeout: Optional[float], atomic: bool = False
) -> RequestSpec:
    name = path.rsplit("/", 1)[-1] or "file"
    params: Dict[str, JsonValue] = {"path": path}
    if atomic:
        params["atomic"] = True
    return RequestSpec(
        "POST",
        "/files/upload",
        params=params,
        files={"file": (name, content)},
        timeout=timeout,
        transfer=True,
    )


def file_bulk_upload_spec(
    files: Mapping[str, bytes], *, timeout: Optional[float], atomic: bool = False
) -> RequestSpec:
    parts: Dict[str, MultipartPart] = {}
    for index, (path, content) in enumerate(files.items()):
        name = path.rsplit("/", 1)[-1] or "file"
        parts[f"files[{index}].path"] = (None, path)
        parts[f"files[{index}].file"] = (name, content)
    params: Dict[str, JsonValue] = {}
    if atomic:
        params["atomic"] = True
    return RequestSpec(
        "POST", "/files/bulk-upload", params=params, files=parts, timeout=timeout, transfer=True
    )


def bulk_download_spec(paths: Sequence[str], *, timeout: Optional[float]) -> RequestSpec:
    return RequestSpec(
        "POST", "/files/bulk-download", json_body={"paths": list(paths)}, timeout=timeout, transfer=True
    )


def parse_bulk_write_result(payload: JsonValue) -> BulkWriteResult:
    obj = expect_object(payload)
    raw_files = obj.get("files")
    files = (
        [
            BulkWriteFile(
                path=str(expect_object(item).get("path", "")),
                bytes=_optional_int(expect_object(item).get("bytes")) or 0,
            )
            for item in raw_files
        ]
        if isinstance(raw_files, list)
        else []
    )
    raw_errors = obj.get("errors")
    errors = (
        [str(item) for item in raw_errors if isinstance(item, str)] if isinstance(raw_errors, list) else []
    )
    return BulkWriteResult(files=files, errors=errors)


def parse_bulk_read_result(content: bytes, *, content_type: Optional[str] = None) -> BulkReadResult:
    files: Dict[str, bytes] = {}
    errors: List[BulkReadError] = []
    for headers, body in _parse_multipart(content, _multipart_boundary(content_type)):
        disposition = headers.get("content-disposition", "")
        part_name = _content_disposition_name(disposition)
        if part_name == "file":
            path = _content_disposition_filename(disposition)
            if path is None:
                raise create_error("multipart file part missing filename", 400, "BAD_REQUEST", "SDK")
            files[path] = body
            continue
        if part_name == "error":
            payload = expect_object(parse_json_bytes(body))
            errors.append(
                BulkReadError(
                    path=_optional_str(payload.get("path")) or "",
                    message=str(payload.get("message") or ""),
                    code=_optional_str(payload.get("code")),
                    source=_optional_str(payload.get("source")),
                )
            )
    return BulkReadResult(files=files, errors=errors)


def list_shape(payload: TransportPayload, *keys: str) -> Tuple[List[JsonObject], Optional[str]]:
    if isinstance(payload, list):
        return [expect_object(item) for item in payload], None
    obj = expect_object(payload)
    raw_items: JsonValue = []
    for key in ("items", *keys):
        candidate = obj.get(key)
        if isinstance(candidate, list):
            raw_items = candidate
            break
    items = [expect_object(item) for item in raw_items] if isinstance(raw_items, list) else []
    cursor = obj.get("nextCursor") or obj.get("next_cursor")
    return items, str(cursor) if isinstance(cursor, str) else None


_SEARCH_LIST_FIELDS = ("sandbox_class", "region")
_SEARCH_TIMESTAMP_FIELDS = (
    "created_start_at",
    "created_end_at",
    "updated_start_at",
    "updated_end_at",
    "last_activity_start_at",
    "last_activity_end_at",
)
_SEARCH_WIRE_NAMES = {"states": "status", "sandbox_class": "class", "region": "region_ids"}


# SDK sandbox state -> the API status names it covers (the search ``status`` filter).
_STATE_WIRE_STATUSES: Dict[str, Tuple[str, ...]] = {
    "queued": ("queued",),
    "creating": ("pending", "assigned"),
    "starting": ("booting",),
    "started": ("running",),
    "pausing": ("pausing",),
    "paused": ("paused",),
    "stopping": ("stopping",),
    "stopped": ("stopped",),
    "destroying": ("deleting",),
    "destroyed": ("deleted",),
    "error": ("failed", "lost"),
}


def _wire_statuses(states: object) -> str:
    """The search ``status`` filter for SDK states: each state's API status names, comma-joined."""
    items = [states] if isinstance(states, str) else list(states) if isinstance(states, Sequence) else None
    if not items:
        raise create_error("states must name at least one sandbox state", 400, "BAD_REQUEST", "SDK")
    wire: list[str] = []
    for state in items:
        statuses = _STATE_WIRE_STATUSES.get(state) if isinstance(state, str) else None
        if statuses is None:
            raise create_error(
                f"states entries must be known sandbox states, got {state!r}", 400, "BAD_REQUEST", "SDK"
            )
        wire.extend(status for status in statuses if status not in wire)
    return ",".join(wire)


def search_timestamp(value: SearchTimestamp) -> str:
    """RFC 3339 form of a search bound; naive datetimes are taken as UTC.

    Anything other than a ``datetime`` or a ``str`` is the SDK's own
    :class:`BadRequestError` (mirrors the TypeScript ``searchTimestamp``, which
    also rejects an invalid ``Date``) — never an ``AttributeError`` from the
    formatter, and no request goes out.
    """
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    raise create_error("timestamp bounds must be a datetime or an RFC 3339 string", 400, "BAD_REQUEST", "SDK")


def _comma_list(name: str, value: Union[str, Sequence[str]]) -> str:
    items = [value] if isinstance(value, str) else list(value)
    # An empty list is not "no filter": the server reads an empty parameter as
    # absent, so `status=[]` would silently become an unfiltered search (and
    # change `list()` pagination). Refuse it here, like `[""]`.
    if not items:
        raise create_error(f"{name} must name at least one value", 400, "BAD_REQUEST", "SDK")
    for item in items:
        if not isinstance(item, str) or not item or "," in item:
            raise create_error(
                f"{name} entries must be non-empty strings without commas", 400, "BAD_REQUEST", "SDK"
            )
    return ",".join(items)


def search_params(query: SearchSandboxesOptions) -> Dict[str, JsonValue]:
    """Wire query for ``GET /sandboxes/search``: lists comma-joined, labels JSON, timestamps RFC 3339."""
    params: Dict[str, JsonValue] = {}
    for f in fields(query):
        value = getattr(query, f.name)
        if value is None:
            continue
        wire = _SEARCH_WIRE_NAMES.get(f.name, f.name)
        if f.name == "states":
            params[wire] = _wire_statuses(value)
        elif f.name in _SEARCH_LIST_FIELDS:
            params[wire] = _comma_list(f.name, value)
        elif f.name == "labels":
            if not isinstance(value, Mapping) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items()
            ):
                raise create_error("labels must be a mapping of string to string", 400, "BAD_REQUEST", "SDK")
            if not value:
                raise create_error("labels must name at least one pair", 400, "BAD_REQUEST", "SDK")
            params[wire] = json.dumps(dict(value), separators=(",", ":"))
        elif f.name in _SEARCH_TIMESTAMP_FIELDS:
            params[wire] = search_timestamp(value)
        elif isinstance(value, bool):
            params[wire] = "true" if value else "false"
        else:
            params[wire] = value
    return params


def parse_search_page(
    payload: JsonValue,
    check_sandbox: Callable[[JsonValue], object],
) -> Tuple[List[JsonObject], Optional[str], int, SearchTotalRelation, str]:
    """Split a ``GET /sandboxes/search`` body into items, cursor, total, and relation.

    The search contract is an object with a ``sandboxes`` array of sandbox
    objects (each checked by ``check_sandbox``, as a detail is), an integer
    ``total``, a string ``total_relation`` and a string or null
    ``next_cursor``. Anything else is the SDK's own :class:`BadRequestError`
    naming the field, never an empty or truncated page — an ``items`` array is
    NOT accepted as a stand-in. Unknown extra fields are tolerated.
    """

    def malformed(detail: str) -> Exception:
        return create_error(f"search response {detail}", 400, "BAD_REQUEST", "SDK")

    if not isinstance(payload, dict):
        raise malformed("is not a JSON object")
    raw_sandboxes = payload.get("sandboxes")
    if not isinstance(raw_sandboxes, list):
        raise malformed("is missing the sandboxes array")
    for item in raw_sandboxes:
        check_sandbox(item)
    items = [expect_object(item) for item in raw_sandboxes]
    total = payload.get("total")
    if total is None:
        raise malformed("is missing its total")
    if isinstance(total, bool) or not isinstance(total, int):
        raise malformed("has a total that is not an integer")
    raw_total_relation = payload.get("total_relation")
    if raw_total_relation is None:
        raise malformed("is missing its total_relation")
    if not isinstance(raw_total_relation, str):
        raise malformed("has a total_relation that is not a string")
    # A cursor of the wrong type would silently end search() / find_by_name() early.
    cursor = payload.get("next_cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise malformed("has a next_cursor that is not a string")
    total_relation: SearchTotalRelation = (
        raw_total_relation if raw_total_relation == "eq" or raw_total_relation == "gte" else "unknown"
    )
    return items, cursor, total, total_relation, raw_total_relation


def sandbox_state(status: str) -> SandboxState:
    states: dict[str, SandboxState] = {
        "queued": "queued",
        "pending": "creating",
        "assigned": "creating",
        "booting": "starting",
        "running": "started",
        "pausing": "pausing",
        "paused": "paused",
        "stopping": "stopping",
        "stopped": "stopped",
        "deleting": "destroying",
        "deleted": "destroyed",
        "failed": "error",
        "lost": "error",
    }
    return states.get(status, "unknown")


def artifact_ref_from_wire(value: object) -> ImageRef | SnapshotRef | None:
    if value is None:
        return None
    if isinstance(value, dict):
        ref_id = value.get("id")
        ref_name = value.get("name")
    else:
        ref_id = getattr(value, "id", None)
        ref_name = getattr(value, "name", None)
    if not isinstance(ref_id, str):
        return None
    name = ref_name if isinstance(ref_name, str) else None
    return ImageRef(id=ref_id, name=name) if ref_id.startswith("img_") else SnapshotRef(id=ref_id, name=name)


def _wire_string(value: object) -> str:
    if isinstance(value, Enum):
        return enum_str(value)
    return value if isinstance(value, str) else ""


def _artifact_retention(value: str) -> ArtifactRetention:
    if value == "permanent" or value == "idle":
        return value
    return "unknown"


def _snapshot_kind(value: str) -> SnapshotKind:
    if value == "full":
        return "full"
    return "unknown"


def _image_source(value: object) -> ImageSource | None:
    match value:
        case {"type": "registry", "ref": str(ref)}:
            return RegistryImageSource(ref=ref)
        case {"type": "dockerfile", "context_object": str(context_object), "dockerfile": str(dockerfile)}:
            return DockerfileImageSource(context_object=context_object, dockerfile=dockerfile)
        case {"type": "named", "name": str(name)}:
            return NamedImageSource(name=name)
        case {"type": "manifest", "name": str(name)}:
            return ManifestImageSource(name=name)
        case _:
            return None


def parse_image(value: object) -> Image:
    data = value.to_dict() if hasattr(value, "to_dict") and callable(value.to_dict) else expect_object(value)
    raw_retention = _wire_string(data.get("retention"))
    return Image(
        id=str(data["id"]),
        name=_optional_str(data.get("name")),
        status=str(data["status"]),
        region=str(data["region_id"]),
        anonymous=data.get("anonymous") is True,
        retention=_artifact_retention(raw_retention),
        raw_retention=raw_retention,
        sandbox_class=_wire_string(data.get("class") or data.get("var_class")),
        available_in=tuple(_optional_str_list(data.get("region_ids")) or ()),
        origin=str(data["origin"]),
        created_at=_required_timestamp(data.get("created_at"), "created_at"),
        updated_at=_required_timestamp(data.get("updated_at") or data.get("created_at"), "updated_at"),
        error=_optional_str(data.get("error")),
        expires_at=parse_timestamp(data.get("expires_at")),
        source=_image_source(data.get("source")),
        bytes=_optional_int(data.get("bytes")),
        layer_count=_optional_int(data.get("layer_count")),
        format_version=_optional_int(data.get("format_version")),
        format_outdated=data.get("format_outdated") is True,
        platform=data.get("platform") is True,
        default=data.get("default") is True,
        deprecated=data.get("deprecated") is True,
        disk_mib=_optional_int(data.get("disk_mib")),
    )


def parse_snapshot(value: object) -> Snapshot:
    data = value.to_dict() if hasattr(value, "to_dict") and callable(value.to_dict) else expect_object(value)
    raw_kind = _wire_string(data.get("kind"))
    raw_class = _wire_string(data.get("class") or data.get("var_class"))
    return Snapshot(
        id=str(data["id"]),
        name=str(data["name"]),
        status=str(data["status"]),
        region=str(data["region_id"]),
        kind=_snapshot_kind(raw_kind),
        raw_kind=raw_kind,
        sandbox_class="linux" if raw_class == "linux" else "unknown",
        raw_sandbox_class=raw_class,
        available_in=tuple(_optional_str_list(data.get("region_ids")) or ()),
        created_at=_required_timestamp(data.get("created_at"), "created_at"),
        updated_at=_required_timestamp(data.get("updated_at") or data.get("created_at"), "updated_at"),
        error=_optional_str(data.get("error")),
        bytes=_optional_int(data.get("bytes")),
        memory_bytes=_optional_int(data.get("memory_bytes")),
        sandbox_id=_optional_str(data.get("sandbox_id")),
    )


def _required_timestamp(value: object, field_name: str) -> datetime:
    parsed = parse_timestamp(value)
    if parsed is None:
        raise create_error(f"API returned an invalid {field_name} timestamp", 400, "BAD_REQUEST", "SDK")
    return parsed


def parse_docker_status(payload: DockerStatusModel) -> DockerStatus:
    raw_owner = enum_str(payload.owner)
    raw_state = enum_str(payload.state)
    return DockerStatus(
        owner=_docker_owner_or_unknown(raw_owner),
        state=_docker_state_or_unknown(raw_state),
        process_id=payload.process_id,
        reason=payload.reason,
        raw_owner=raw_owner,
        raw_state=raw_state,
    )


def gib_to_mib(gib: object, *, label: str) -> int:
    """GiB as the wire's MiB: ``gib * 1024`` must be a whole number, never rounded."""
    mib = gib * 1024 if isinstance(gib, (int, float)) and not isinstance(gib, bool) else math.nan
    # An int needs no float check: math.isfinite() and int() raise OverflowError past the float range.
    if not (isinstance(mib, int) or (math.isfinite(mib) and mib == int(mib))):
        raise create_error(
            f"{label} must be a GiB value that converts to a whole number of MiB", 400, "BAD_REQUEST", "SDK"
        )
    # A size is positive, as the cpu values are.
    if mib <= 0:
        raise create_error(f"{label} must be a positive number of GiB", 400, "BAD_REQUEST", "SDK")
    return int(mib)


def mib_to_gib(mib: object) -> Optional[float]:
    """The wire's MiB as GiB, exactly (``mib / 1024``); ``None`` when absent."""
    if not isinstance(mib, int) or isinstance(mib, bool):
        return None
    return mib / 1024


def snapshot_state(image_status: str, dockerfile: bool = False) -> str:
    if image_status == "pending":
        return "pending"
    if image_status == "building":
        return "building"
    if image_status == "ready":
        return "active"
    if image_status == "failed":
        return "build_failed" if dockerfile else "error"
    return "error"


def _parse_log_frame(payload: LogFrameDto) -> LogFrame:
    return LogFrame(
        cursor=payload.cursor,
        channel=payload.channel,
        data=payload.data,
        timestamp=payload.timestamp,
    )


def _parse_terminal(value: ToolboxTerminalSize | None) -> Optional[TerminalSize]:
    if value is None:
        return None
    return TerminalSize(cols=value.cols, rows=value.rows)


def _optional_str_list(value: JsonValue) -> Optional[List[str]]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return None


def _find_multipart_delimiter(body: bytes, marker: bytes, start: int) -> int:
    """Locate the next real delimiter, skipping look-alikes inside a part body.

    RFC 2046 only recognises ``CRLF--boundary`` when it is followed by CRLF (next
    part) or ``--`` (close). A file whose bytes happen to contain the boundary
    followed by anything else is payload, so keep scanning past it.
    """
    delimiter = b"\r\n" + marker
    candidate = body.find(delimiter, start)
    while candidate != -1:
        after = candidate + len(delimiter)
        if body[after : after + 2] in (b"\r\n", b"--"):
            return candidate
        candidate = body.find(delimiter, candidate + 1)
    return -1


def _parse_multipart(body: bytes, boundary: str) -> List[Tuple[Dict[str, str], bytes]]:
    marker = f"--{boundary}".encode("ascii")
    if not body.startswith(marker):
        raise create_error("invalid multipart response boundary", 400, "BAD_REQUEST", "SDK")
    parts: List[Tuple[Dict[str, str], bytes]] = []
    position = len(marker)
    while position < len(body):
        if body[position : position + 2] == b"--":
            return parts
        if body[position : position + 2] != b"\r\n":
            raise create_error("invalid multipart response delimiter", 400, "BAD_REQUEST", "SDK")
        header_start = position + 2
        header_end = body.find(b"\r\n\r\n", header_start)
        if header_end == -1:
            raise create_error("invalid multipart response headers", 400, "BAD_REQUEST", "SDK")
        headers = _parse_multipart_headers(body[header_start:header_end])
        body_start = header_end + 4
        next_boundary = _find_multipart_delimiter(body, marker, body_start)
        if next_boundary == -1:
            raise create_error("unterminated multipart response", 400, "BAD_REQUEST", "SDK")
        parts.append((headers, body[body_start:next_boundary]))
        position = next_boundary + 2 + len(marker)
    raise create_error("unterminated multipart response", 400, "BAD_REQUEST", "SDK")


def _multipart_boundary(content_type: Optional[str]) -> str:
    if content_type is None:
        return DAYTONA_FILE_BOUNDARY
    match = _MULTIPART_BOUNDARY_RE.search(content_type)
    if match is None:
        return DAYTONA_FILE_BOUNDARY
    boundary = match.group(1) or match.group(2) or DAYTONA_FILE_BOUNDARY
    return boundary.strip() or DAYTONA_FILE_BOUNDARY


def _parse_multipart_headers(raw_headers: bytes) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for header_line in raw_headers.decode("latin-1").split("\r\n"):
        name, sep, value = header_line.partition(":")
        if not sep:
            continue
        headers[name.strip().lower()] = value.strip()
    return headers


def _content_disposition_name(header: str) -> Optional[str]:
    match = _CONTENT_DISPOSITION_NAME_RE.search(header)
    return match.group(1) if match is not None else None


def _content_disposition_filename(header: str) -> Optional[str]:
    filename_star = _CONTENT_DISPOSITION_FILENAME_STAR_RE.search(header)
    if filename_star is not None:
        return unquote(filename_star.group(1), encoding="utf-8", errors="replace")
    filename = _CONTENT_DISPOSITION_FILENAME_RE.search(header)
    return filename.group(1) if filename is not None else None
