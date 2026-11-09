# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import calendar
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
from enum import Enum
from typing import (
    AsyncIterable,
    AsyncIterator,
    Dict,
    Final,
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
    KillProcessesResponse,
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
    SearchOrder,
    SearchSandboxesOptions,
    SearchSort,
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
    # The cap on this response's buffered body, replacing the client's
    # ``max_response_bytes`` for this request (``0``: no limit); ``None`` keeps
    # the client's. Streamed bodies are never capped.
    max_response_bytes: Optional[int] = None
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


def _too_deep(description: str) -> DaytonaError:
    return create_error(f"expected {description}: the JSON is nested too deeply", 400, "BAD_REQUEST", "SDK")


def _refuse_json_constant(name: str) -> JsonValue:
    """``NaN``, ``Infinity`` and ``-Infinity`` are not JSON; ``JSON.parse``
    refuses them too."""
    raise ValueError(f"invalid JSON constant {name}")


# The deepest nesting of arrays and objects a JSON response may have, the
# same in both SDKs: the stdlib decoder's own limit depends on the Python
# version and, from 3.12, on the thread's stack size, so the SDK sets one.
# Deep enough for every contract shape (a 256-level accessibility tree is
# 516 levels of JSON).
JSON_MAX_DEPTH: Final = 600


def _nested_deeper_than(value: JsonValue, limit: int) -> bool:
    """Whether arrays and objects in ``value`` nest more than ``limit`` deep
    (a top-level one is depth 1). Iterative, visiting containers only."""
    pending: List[Tuple[JsonValue, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else None
        if children is None:
            continue
        if depth > limit:
            return True
        pending.extend((child, depth + 1) for child in children if isinstance(child, (list, dict)))
    return False


def loads_json(text: Union[str, bytes], description: str = "JSON response") -> JsonValue:
    """``json.loads`` for a response body: a body nested deeper than
    :data:`JSON_MAX_DEPTH`, or than the decoder's recursion limit, is the
    malformed-response error, never a raw ``RecursionError``. Invalid JSON,
    including the non-standard constants ``NaN``, ``Infinity`` and
    ``-Infinity``, raises ``ValueError``."""
    try:
        value: JsonValue = json.loads(text, parse_constant=_refuse_json_constant)
    except RecursionError:
        raise _too_deep(description) from None
    if _nested_deeper_than(value, JSON_MAX_DEPTH):
        raise _too_deep(description)
    return value


def parse_json_bytes(content: bytes) -> JsonValue:
    if not content:
        return {}
    return loads_json(content.decode("utf-8"))


_CPU_ARCHITECTURES = ("amd64", "arm64")


def image_options_body(
    *, name: object = None, idle_expiry_seconds: object = None, region: object = None, cpu_arch: object = None
) -> JsonObject:
    """The image options of a build, pull or capture as wire fields, each
    checked before any request (as in TypeScript); ``None`` is omitted."""
    body: JsonObject = {}
    for key, wire, value in (
        ("name", "name", name),
        ("region", "region_id", region),
        ("cpu_arch", "cpu_arch", cpu_arch),
    ):
        if key == "region":
            # The region rule of create: blank or local selects the default
            # region (no region_id); any other value follows the header rule
            # and is sent as given.
            selected = region_selector(value)
            if selected is not None:
                body[wire] = selected
            continue
        if value is None:
            continue
        if not isinstance(value, str):
            raise create_error(f"{key} must be a string", 400, "BAD_REQUEST", "SDK")
        # A blank name names nothing: refused, as promote() refuses it.
        if key == "name" and not value.strip():
            raise create_error("name must be a non-empty string", 400, "BAD_REQUEST", "SDK")
        # The contract's architectures only: SDK inputs are closed.
        if key == "cpu_arch" and value not in _CPU_ARCHITECTURES:
            raise create_error('cpu_arch must be "amd64" or "arm64"', 400, "BAD_REQUEST", "SDK")
        body[wire] = value
    if idle_expiry_seconds is not None:
        # The contract: -1 (the maximum) or 1 and up, an int64 both SDKs carry
        # exactly; 0 and anything below -1 are refused, and the server holds
        # the maximum.
        if (
            isinstance(idle_expiry_seconds, bool)
            or not isinstance(idle_expiry_seconds, int)
            or not (idle_expiry_seconds == -1 or 1 <= idle_expiry_seconds <= MAX_SAFE_INTEGER)
        ):
            raise create_error(
                "idle_expiry_seconds must be -1 (the maximum) or a positive integer number of seconds",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        body["idle_expiry_seconds"] = idle_expiry_seconds
    return body


def parse_json_response(content: bytes, *, what: str) -> JsonValue:
    """A JSON response body; one that is not JSON (or not UTF-8) breaks the
    contract: the SDK's :class:`BadRequestError` naming the response."""
    if not content:
        return {}
    try:
        # A body nested too deeply names the response too.
        return loads_json(content.decode("utf-8"), what)
    except ValueError as error:
        raise create_error(f"{what} is not JSON", 400, "BAD_REQUEST", "SDK") from error


def error_from_response(status_code: int, body: bytes) -> DaytonaError:
    try:
        payload = expect_object(parse_json_bytes(body))
    except Exception:
        payload = {"message": body.decode("utf-8", errors="replace") or f"HTTP {status_code}"}
    return parse_error_payload(status_code, payload)


def is_json_value(value: object) -> TypeGuard[JsonValue]:
    """Structural check for a JSON value. Iterative, so a deeply nested value
    cannot exhaust the stack; a list or dict that contains itself is not
    JSON, and one shared by several parents is checked once."""
    # id -> False while a container's children are being checked, True after.
    checked: Dict[int, bool] = {}
    pending: List[Tuple[object, bool]] = [(value, False)]
    while pending:
        item, leaving = pending.pop()
        if leaving:
            checked[id(item)] = True
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            continue
        if not isinstance(item, (list, dict)):
            return False
        state = checked.get(id(item))
        if state is not None:
            if state:
                continue
            return False
        children: Iterable[object]
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                return False
            children = item.values()
        else:
            children = item
        checked[id(item)] = False
        pending.append((item, True))
        pending.extend((child, False) for child in children)
    return True


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
            # `path` ends in the list's `.`: an item reads `items[0].key`.
            missing = _missing_required_key(item, model_type, f"{path.removesuffix('.')}[{index}].")
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


def non_string_timestamp(value: object, model_type: object, path: str = "") -> Optional[str]:
    """The path of the first date or date-time field of a generated model
    (nested models and list items included) whose value is present, not
    ``null`` and not a string, or ``None``.

    The contract's timestamps are strings, and TypeScript refuses anything
    else; the generated models would read a number as a Unix time.
    """
    model_fields = getattr(model_type, "model_fields", None)
    if not isinstance(value, dict) or not isinstance(model_fields, dict):
        return None
    for name, info in model_fields.items():
        key = info.alias or name
        child = value.get(key)
        if child is not None:
            found = _non_string_timestamp_in(child, info.annotation, f"{path}{key}")
            if found is not None:
                return found
    return None


def _non_string_timestamp_in(value: object, annotation: object, path: str) -> Optional[str]:
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = None if item is None else _non_string_timestamp_in(item, annotation, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    if _names_date(annotation) and not isinstance(value, str):
        return path
    for candidate in _model_types(annotation):
        found = non_string_timestamp(value, candidate, f"{path}.")
        if found is not None:
            return found
    return None


def _names_date(annotation: object) -> bool:
    """Whether an annotation is a date or date-time (through Optional / List)."""
    return annotation in (date, datetime) or any(_names_date(argument) for argument in get_args(annotation))


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
            raise create_error(
                f"expected {description}: {missing}: Field required", 400, "BAD_REQUEST", "SDK"
            )
        timestamp = non_string_timestamp(value, model_type)
        if timestamp is not None:
            raise create_error(
                f"{description} has a {timestamp} that is not a string", 400, "BAD_REQUEST", "SDK"
            )
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


def expect_kill_processes_response(value: TransportPayload) -> KillProcessesResponse:
    return _expect_model_from_dict(
        value, KillProcessesResponse, "kill processes response", KillProcessesResponse.from_dict
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
    tag: Optional[str] = None,
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
        "tag": tag,
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
        tag=payload.tag,
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
    tag: Optional[str] = None,
) -> RequestSpec:
    params: Dict[str, JsonValue] = {}
    for key, value in {
        "state": state,
        "kind": kind,
        "sessionId": session_id,
        "name": name,
        "pid": pid,
        "tag": tag,
    }.items():
        if value is not None:
            params[key] = value
    return RequestSpec("GET", "/processes", params=params)


def process_kill_spec(*, tag: str, timeout: Optional[float]) -> RequestSpec:
    body: JsonObject = {"tag": tag}
    if timeout is not None:
        body["timeoutMs"] = timeout_millis_from_seconds(timeout)
    return RequestSpec(
        "POST",
        "/processes:kill",
        json_body=body,
        timeout=(10.0 if timeout is None else timeout) + WAIT_READ_SLACK_SECONDS,
    )


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


#: The longest delay a timer can arm, as the transport's bound: a wait budget
#: above it means no deadline.
MAX_WAIT_BUDGET_SECONDS: float = 2_147_483.647
#: The same bound in whole seconds: the largest wait a request body carries
#: (both SDKs send this for a wait with no deadline).
MAX_WIRE_WAIT_SECONDS: int = 2_147_483
#: The contract's int32 ceiling (MiB sizes, ports).
INT32_MAX: int = 2_147_483_647
#: The largest integer both SDKs represent exactly (an int64 the API accepts).
MAX_SAFE_INTEGER: int = 2**53 - 1


def finite_number(value: object) -> TypeGuard[Union[int, float]]:
    """Whether ``value`` is a number a double can carry: an ``int`` or
    ``float`` (not a ``bool``) that is finite. An ``int`` too large for a
    float, which ``JSON.parse`` reads as ``Infinity``, is not; the check
    converts as ``float()`` does, so it never raises ``OverflowError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def wait_budget_seconds(value: object, *, name: str = "timeout") -> float:
    """A wait budget in seconds: ``0.0`` for no deadline (``0``, or a value
    above the timer maximum, an integer too large for a float included), else
    the budget. Anything but a non-negative finite number is the SDK's
    :class:`BadRequestError`; integers are compared without converting them,
    so a huge one never overflows.
    """
    valid = (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value >= 0
        and (isinstance(value, int) or math.isfinite(value))
    )
    if not valid:
        raise create_error(
            f"{name} must be a non-negative finite number of seconds", 400, "BAD_REQUEST", "SDK"
        )
    assert isinstance(value, (int, float))
    return 0.0 if value > MAX_WAIT_BUDGET_SECONDS else float(value)


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
_SEARCH_BOOLEAN_FIELDS = ("spot",)
_SEARCH_NUMBER_FIELDS = ("min_cores", "max_cores")
_SEARCH_INTEGER_FIELDS = ("min_memory_mib", "max_memory_mib", "min_disk_mib", "max_disk_mib")
_SEARCH_CHOICES: Dict[str, Tuple[str, ...]] = {
    "sort": get_args(SearchSort),
    "order": get_args(SearchOrder),
}
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


_RFC3339 = re.compile(
    r"(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)",
    re.ASCII,
)


def _rfc3339(value: str) -> bool:
    """An RFC 3339 timestamp without a leap second, as TypeScript checks it: year 0001-9999,
    the day within its month (Feb 29 only in a leap year), hour 00-23, minute
    and second 00-59 (no leap second: neither a ``datetime`` nor a ``Date``
    can express one), an optional fraction, and ``Z`` or an offset within
    +-23:59."""
    match = _RFC3339.fullmatch(value)
    if match is None:
        return False
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return year >= 1 and day <= calendar.monthrange(year, month)[1]


def search_timestamp(value: SearchTimestamp) -> str:
    """The wire form of a search bound, as TypeScript's ``searchTimestamp``.

    A string is checked (an RFC 3339 timestamp without a leap second) and sent
    exactly as written. A ``datetime`` is sent in UTC, so its UTC instant must
    fall in years 0001-9999; a naive one (``utcoffset()`` is ``None``, a
    ``tzinfo`` that returns ``None`` included) is taken as UTC. Anything else,
    and a ``datetime`` whose conversion fails (its ``tzinfo`` raising, an
    instant outside years 0001-9999 in UTC) or whose rendered form fails the
    string rule, is the same :class:`BadRequestError`, before any request: as
    TypeScript's ``Date`` branch, the rendered form is checked like a string.
    """
    invalid = create_error(
        "timestamp bounds must be a datetime or an RFC 3339 timestamp without a leap second"
        " (year 0001-9999, a real calendar date)",
        400,
        "BAD_REQUEST",
        "SDK",
    )
    if isinstance(value, datetime):
        rendered: object
        try:
            # Only the calls on the caller's object: a tzinfo may raise anything,
            # and a datetime subclass may render anything.
            aware = value if value.utcoffset() is not None else value.replace(tzinfo=timezone.utc)
            rendered = aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception as error:
            raise invalid from error
        if isinstance(rendered, str) and _rfc3339(rendered):
            return rendered
        raise invalid
    # An RFC 3339 timestamp without a leap second (the contract's date-time), the same rule TypeScript checks.
    if isinstance(value, str) and _rfc3339(value):
        return value
    raise invalid


def _comma_list(name: str, value: object) -> str:
    # A string or a sequence of strings (not a mapping, whose keys would be
    # sent), as TypeScript takes a string or an array.
    if isinstance(value, str):
        items: list[object] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value)
    else:
        raise create_error(f"{name} must be a string or a list of strings", 400, "BAD_REQUEST", "SDK")
    # An empty list is not "no filter": the server reads an empty parameter as
    # absent, so `status=[]` would silently become an unfiltered search (and
    # change `list()` pagination). Refuse it here, like `[""]`.
    if not items:
        raise create_error(f"{name} must name at least one value", 400, "BAD_REQUEST", "SDK")
    texts: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or "," in item:
            raise create_error(
                f"{name} entries must be non-empty strings without commas", 400, "BAD_REQUEST", "SDK"
            )
        texts.append(item)
    return ",".join(texts)


def has_surrogate(value: str) -> bool:
    """Whether ``value`` holds a surrogate code point, which has no UTF-8 form
    (the HTTP client would raise ``UnicodeEncodeError`` encoding it)."""
    return any("\ud800" <= char <= "\udfff" for char in value)


def query_text(value: str, name: str) -> str:
    """A caller string sent as a query value: one with a lone surrogate is
    :class:`BadRequestError` naming ``name``, before any request, as in TypeScript."""
    if has_surrogate(value):
        raise create_error(f"{name} must not contain a lone surrogate", 400, "BAD_REQUEST", "SDK")
    return value


def header_text(value: str, name: str) -> str:
    """A caller string sent as a header value: non-empty printable ASCII with
    no leading or trailing whitespace, else :class:`BadRequestError` naming
    ``name``, before any request. It is never stripped: an idempotency key or
    an ETag the SDK altered would not be the caller's value. TypeScript applies
    the same rule (``headerText``), so both SDKs refuse the same values:
    neither sends header bytes the other would not, and neither fails with a
    transport error (the HTTP client refuses padded header values) for one.
    ``region`` is checked through :func:`region_selector`, which first takes a
    blank value or ``local`` as the default region (not sent)."""
    if not value:
        raise create_error(f"{name} must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    if not all(" " <= char <= "~" for char in value) or value[0] == " " or value[-1] == " ":
        raise create_error(
            f"{name} must be printable ASCII with no leading or trailing whitespace",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return value


def region_selector(region: object) -> Optional[str]:
    """The region ``create*()``, ``create_from_build()``, ``find_by_name()``
    and ``images.build()`` / ``pull()`` select: ``None``, a blank or
    whitespace-only string, or ``local`` in any letter case and with any
    surrounding whitespace select the default region and are not sent (no
    ``x-daytona-region`` header, no ``region_id`` field); any other value is
    sent as given and must follow :func:`header_text`'s rule (printable ASCII,
    no leading or trailing whitespace). As TypeScript's ``selectRegion``. The
    reservation calls send ``region`` as given, since their contract reads a
    blank value or ``local`` as no region; the search and Mac-catalog filters
    list regions, where ``local`` has no meaning."""
    if region is None:
        return None
    if not isinstance(region, str):
        raise create_error("region must be a string", 400, "BAD_REQUEST", "SDK")
    if not region.strip() or region.strip().lower() == "local":
        return None
    return header_text(region, "region")


def path_segment(value: object, name: str) -> str:
    """A caller's id or name as one request-path segment: a string that is not
    blank (Python's whitespace) and has no surrogate code point (no UTF-8
    form; ``quote()`` would raise ``UnicodeEncodeError``) and is not ``.`` or
    ``..`` (dot segments, which the HTTP client resolves away, so the request
    would reach another route), else :class:`BadRequestError` naming
    ``name``, before any request, as TypeScript's ``pathSegment``."""
    if not isinstance(value, str) or not value.strip():
        raise create_error(f"{name} must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    if value in (".", ".."):
        raise create_error(f"{name} must not be '.' or '..'", 400, "BAD_REQUEST", "SDK")
    if has_surrogate(value):
        raise create_error(f"{name} must not contain a lone surrogate", 400, "BAD_REQUEST", "SDK")
    return quote(value, safe="")


def page_params(cursor: object, limit: object) -> Dict[str, JsonValue]:
    """The ``cursor`` / ``limit`` query of a list: a string cursor and an
    integer limit from 1 to 200 (the contract's range), else
    :class:`BadRequestError` naming the parameter, before any request."""
    params: Dict[str, JsonValue] = {}
    if cursor is not None:
        if not isinstance(cursor, str):
            raise create_error("cursor must be a string", 400, "BAD_REQUEST", "SDK")
        params["cursor"] = query_text(cursor, "cursor")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise create_error("limit must be an integer from 1 to 200", 400, "BAD_REQUEST", "SDK")
        params["limit"] = limit
    return params


def optional_query_text(value: object, name: str) -> Optional[str]:
    """A string query filter, or ``None``; anything else is :class:`BadRequestError`."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise create_error(f"{name} must be a string", 400, "BAD_REQUEST", "SDK")
    return query_text(value, name)


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
            joined = _comma_list(f.name, value)
            # The contract's classes only: SDK inputs are closed.
            if f.name == "sandbox_class" and any(
                item not in ("linux", "macos") for item in joined.split(",")
            ):
                raise create_error(
                    'sandbox_class entries must be "linux" or "macos"', 400, "BAD_REQUEST", "SDK"
                )
            params[wire] = query_text(joined, f.name)
        elif f.name == "labels":
            if not isinstance(value, Mapping) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items()
            ):
                raise create_error("labels must be a mapping of string to string", 400, "BAD_REQUEST", "SDK")
            if not value:
                raise create_error("labels must name at least one pair", 400, "BAD_REQUEST", "SDK")
            params[wire] = json.dumps(dict(value), separators=(",", ":"))
        elif f.name in _SEARCH_TIMESTAMP_FIELDS:
            params[wire] = query_text(search_timestamp(value), f.name)
        elif f.name in _SEARCH_BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise create_error(f"{f.name} must be a boolean", 400, "BAD_REQUEST", "SDK")
            params[wire] = "true" if value else "false"
        elif f.name in _SEARCH_NUMBER_FIELDS:
            if not finite_number(value):
                raise create_error(f"{f.name} must be a finite number", 400, "BAD_REQUEST", "SDK")
            params[wire] = value
        elif f.name in _SEARCH_INTEGER_FIELDS:
            # An int64 both SDKs carry exactly.
            if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_SAFE_INTEGER:
                raise create_error(f"{f.name} must be an integer", 400, "BAD_REQUEST", "SDK")
            params[wire] = value
        elif f.name in _SEARCH_CHOICES:
            # The contract's values only: SDK inputs are closed.
            if value not in _SEARCH_CHOICES[f.name]:
                choices = ", ".join(_SEARCH_CHOICES[f.name])
                raise create_error(f"{f.name} must be one of {choices}", 400, "BAD_REQUEST", "SDK")
            params[wire] = value
        elif f.name in ("cursor", "limit"):
            params.update(
                page_params(value if f.name == "cursor" else None, value if f.name == "limit" else None)
            )
        else:
            params[wire] = optional_query_text(value, f.name)
    return params


def parse_search_page(
    payload: JsonValue,
    check_sandbox: Callable[[JsonValue], object],
) -> Tuple[List[JsonObject], Optional[str], int, SearchTotalRelation, str]:
    """Split a ``GET /sandboxes/search`` body into items, cursor, total, and relation.

    The search contract is an object with a ``sandboxes`` array of sandbox
    objects (each checked by ``check_sandbox``, as a detail is), a
    non-negative integer ``total``, a string ``total_relation`` and a string or null
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
    if not isinstance(total, int) or not finite_number(total):
        raise malformed("has a total that is not an integer")
    if total < 0:
        # The contract's int64 with minimum 0.
        raise malformed("has a negative total")
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
    if not isinstance(mib, int) or isinstance(mib, bool) or not finite_number(mib):
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
