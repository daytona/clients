# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import collections.abc
import functools
import json
import math
import re
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
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
)
from urllib.parse import quote, unquote

from pydantic import BaseModel, ValidationError

from ._gen.api import LifecyclePolicy
from ._gen.toolbox import (
    BranchList,
    BulkUploadResponse,
    ExitResult,
    IsPortInUseResponse,
    LogFrameDto,
    PortList,
    ProcessDto,
    ProcessListResponse,
    ProcessLogsResponse,
    SearchResponse,
    SessionDto,
    SessionListResponse,
)
from ._gen.toolbox import (
    CommitResponse as CommitResponseModel,
)
from ._gen.toolbox import (
    DockerStatus as DockerStatusModel,
)
from ._gen.toolbox import (
    FileInfo as FileInfoModel,
)
from ._gen.toolbox import (
    Match as MatchModel,
)
from ._gen.toolbox import (
    Metrics as MetricsModel,
)
from ._gen.toolbox import (
    ReplaceResult as ReplaceResultModel,
)
from ._gen.toolbox import (
    StatusResponse as StatusResponseModel,
)
from ._gen.toolbox import (
    TerminalSize as ToolboxTerminalSize,
)
from ._timestamps import parse_optional_timestamp, parse_timestamp
from .errors import DaytonaError, UnsupportedOperationError, create_error
from .types import (
    DISABLED,
    ArtifactRetention,
    BulkReadError,
    BulkReadResult,
    BulkWriteFile,
    BulkWriteResult,
    DockerStatus,
    FileInfo,
    FileMatch,
    GitCommitResponse,
    GitFileStatus,
    GitStatus,
    Image,
    ImageRef,
    ImageSource,
    JsonObject,
    JsonValue,
    KeepLogs,
    ListResult,
    LogFrame,
    LogPage,
    ProcessInfo,
    ProcessKind,
    ProcessStateFilter,
    ReplaceResult,
    SandboxLifecycle,
    SandboxSearchFilters,
    SandboxState,
    SearchSandboxesQuery,
    SearchTimestamp,
    SearchTotalRelation,
    Snapshot,
    SnapshotKind,
    SnapshotRef,
    StdinMode,
    SystemMetrics,
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
ProcessLogPayload = Union[TransportPayload, ProcessLogsResponse]
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


def is_json_media_type(content_type: Optional[str]) -> bool:
    """Whether a response's media type is exactly ``application/json`` (type
    and subtype compared case-insensitively, parameters ignored), as the
    TypeScript SDK's ``isJsonMediaType``: ``application/jsonl`` is not JSON."""
    if content_type is None:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


def refuse_dot_segment(value: str, name: str) -> None:
    """``.`` and ``..`` are no ids: as a request-path segment the HTTP client
    resolves them away, so the request would reach another route."""
    if value in (".", ".."):
        raise create_error(f"{name} must not be '.' or '..'", 400, "BAD_REQUEST", "SDK")


def toolbox_path_id(value: str, name: str) -> str:
    """A toolbox id or name as one percent-encoded request-path segment (see
    :func:`refuse_dot_segment`). A lone surrogate code point has no UTF-8
    form, so it is refused too, as TypeScript's ``toolboxPathId`` does;
    astral characters are kept."""
    refuse_dot_segment(value, name)
    if any("\ud800" <= char <= "\udfff" for char in value):
        raise create_error(f"{name} must not contain a lone surrogate", 400, "BAD_REQUEST", "SDK")
    return quote(value, safe="")


def parse_json_bytes(content: bytes) -> JsonValue:
    if not content:
        return {}
    return loads_json(content.decode("utf-8"))


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
    _check_container_fields(value, model_type, description)
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


# (wire key, "list" | "dict" | "model", the generated model inside it or None)
# (wire key, "list" | "dict" | "model" | "number", the generated model inside
# it or None, "integer" | "number" for a number field or a list / map of
# numbers, else None)
_ContainerField = Tuple[str, str, Optional[type], Optional[str]]


def _number_kind(annotation: object) -> Optional[str]:
    """``"integer"`` for an int-typed annotation, ``"number"`` for a float
    (or float-or-int) one, else ``None``; ``Optional`` and ``Annotated``
    wrappers are looked through."""
    if typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    if typing.get_origin(annotation) in (Union, types.UnionType):
        kinds = {_number_kind(arg) for arg in typing.get_args(annotation) if arg is not type(None)}
        if kinds == {"integer"}:
            return "integer"
        return "number" if kinds and kinds <= {"integer", "number"} else None
    if annotation is bool:
        return None
    if annotation is int:
        return "integer"
    return "number" if annotation is float else None


@functools.cache
def _container_fields(model_type: type) -> Tuple[_ContainerField, ...]:
    """The list, map, nested-model and number fields of a generated model,
    read from its field types. A oneOf wrapper (a model holding
    ``actual_instance``) is no nested object: its value may be a string or a
    list, which the model itself decides."""
    found: List[_ContainerField] = []
    model_fields = getattr(model_type, "model_fields", {})
    for name, info in model_fields.items():
        if name == "additional_properties":
            continue
        key = info.alias or name
        annotation = info.annotation
        number = _number_kind(annotation)
        if number is not None:
            found.append((key, "number", None, number))
            continue
        if typing.get_origin(annotation) in (Union, types.UnionType):
            present = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
            if len(present) != 1:
                continue
            annotation = present[0]
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        inner = args[-1] if args else None
        inner_model = inner if _plain_model(inner) else None
        if origin is list:
            found.append((key, "list", inner_model, _number_kind(inner)))
        elif origin is dict:
            found.append((key, "dict", inner_model, _number_kind(inner)))
        elif _plain_model(annotation):
            found.append((key, "model", annotation, None))
    return tuple(found)


def _refuse_non_finite(item: object, kind: Optional[str], path: _FieldPath, description: str) -> None:
    """A number a double cannot carry (an ``int`` beyond the float range, a
    float decoded to infinity) is malformed, as ``JSON.parse`` reads it as
    ``Infinity``; worded as the TypeScript SDK words it."""
    if kind is None or isinstance(item, bool) or not isinstance(item, (int, float)) or finite_number(item):
        return
    expected = "an integer" if kind == "integer" else "a number"
    raise create_error(
        f"expected {description}: {_render_path(path)} must be {expected}", 400, "BAD_REQUEST", "SDK"
    )


def _plain_model(annotation: object) -> TypeGuard[type]:
    return (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and "actual_instance" not in annotation.model_fields
    )


# A field's path as a parent-linked chain, rendered only for an error, so a
# deeply nested payload costs no per-level string copies.
_FieldPath = Optional[Tuple[str, "_FieldPath"]]


def _render_path(path: _FieldPath) -> str:
    """The path, its innermost 16 segments only (``…`` marks a cut)."""
    parts: List[str] = []
    while path is not None and len(parts) < 16:
        parts.append(path[0])
        path = path[1]
    return ("…" if path is not None else "") + "".join(reversed(parts))


def _check_container_fields(value: JsonObject, model_type: type, description: str) -> None:
    """Every list, map, nested-object and number field of a generated model,
    nested models included, has its JSON shape before the model decodes it,
    named in the error: the generated ``from_dict`` iterates whatever is
    there (``{}`` or ``""`` would become ``[]``, a number a raw
    ``TypeError``), a nested model's own error would not name the field, and
    a number a double cannot carry is refused (see :func:`finite_number`).
    Other values are left to the models. Walks the payload with an explicit
    stack, never recursing."""
    stack: List[Tuple[Mapping[str, JsonValue], type, _FieldPath]] = [(value, model_type, None)]
    while stack:
        raw, model, parent = stack.pop()
        for key, shape, inner, number in _container_fields(model):
            item = raw.get(key)
            if item is None:
                continue
            path: _FieldPath = (key if parent is None else f".{key}", parent)
            if shape == "number":
                _refuse_non_finite(item, number, path, description)
            elif shape == "list":
                if not isinstance(item, list):
                    raise create_error(
                        f"expected {description}: {_render_path(path)} must be a list",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                for index, element in enumerate(item):
                    if inner is not None and isinstance(element, dict):
                        stack.append((element, inner, (f"[{index}]", path)))
                    else:
                        _refuse_non_finite(element, number, (f"[{index}]", path), description)
            elif shape == "dict":
                if not isinstance(item, dict):
                    raise create_error(
                        f"expected {description}: {_render_path(path)} must be an object",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                for name, element in item.items():
                    if inner is not None and isinstance(element, dict):
                        stack.append((element, inner, (f".{name}", path)))
                    else:
                        _refuse_non_finite(element, number, (f".{name}", path), description)
            elif isinstance(item, dict):
                if inner is not None:
                    stack.append((item, inner, path))
            else:
                raise create_error(
                    f"expected {description}: {_render_path(path)} must be an object",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )


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
            timeout_at=model.timeout_at,
            idle_expiry_seconds=model.idle_expiry_seconds,
            on_idle_timeout=model.on_idle_timeout.value if model.on_idle_timeout is not None else None,
            idle_timeout_at=model.idle_timeout_at,
            last_activity_at=model.last_activity_at,
            last_activity_source=model.last_activity_source,
        )
    raw_payload = payload
    nested = raw_payload.get("lifecycle_policy")
    data: JsonObject = nested if isinstance(nested, dict) else raw_payload
    return SandboxLifecycle(
        timeout_seconds=_optional_int(data.get("timeout_seconds")),
        on_timeout=_optional_str(data.get("on_timeout")),
        timeout_at=_optional_str(data.get("timeout_at")),
        idle_expiry_seconds=_optional_int(data.get("idle_expiry_seconds")),
        on_idle_timeout=_optional_str(data.get("on_idle_timeout")),
        idle_timeout_at=_optional_str(data.get("idle_timeout_at")),
        last_activity_at=_optional_str(data.get("last_activity_at")),
        last_activity_source=_optional_str(data.get("last_activity_source")),
    )


def _lifecycle_enabled(body: JsonObject, name: str) -> bool:
    return name in body and body[name] != DISABLED


def _optional_int(value: JsonValue) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: JsonValue) -> Optional[str]:
    return value if isinstance(value, str) else None


def expect_branch_list(value: TransportPayload) -> BranchList:
    return _expect_model_from_dict(value, BranchList, "branch list response", BranchList.from_dict)


def expect_commit_response(value: TransportPayload) -> GitCommitResponse:
    model = _expect_model_from_dict(
        value, CommitResponseModel, "commit response", CommitResponseModel.from_dict
    )
    return GitCommitResponse(hash=model.hash)


def expect_exit_result(value: TransportPayload) -> ExitResult:
    return _expect_model_from_dict(value, ExitResult, "wait result", ExitResult.from_dict)


def expect_file_info(value: TransportPayload) -> FileInfo:
    model = _expect_model_from_dict(value, FileInfoModel, "file info response", FileInfoModel.from_dict)
    return FileInfo(
        name=model.name,
        path=model.path,
        size=model.size,
        is_dir=model.is_dir,
        mode=model.mode,
        permissions=model.permissions,
        owner=model.owner,
        group=model.group,
        modified_at=parse_timestamp(model.modified_at, "file info response: modifiedAt"),
        mod_time=model.mod_time,
    )


def _cpu_utilization(value: float) -> float:
    """A fraction between 0 and 1 inclusive, as the contract states."""
    if not 0 <= value <= 1:
        raise create_error(
            "expected system metrics: cpuUtilization must be between 0 and 1", 400, "BAD_REQUEST", "SDK"
        )
    return value


def expect_system_metrics(value: TransportPayload) -> SystemMetrics:
    model = _expect_model_from_dict(value, MetricsModel, "system metrics", MetricsModel.from_dict)
    return SystemMetrics(
        timestamp=parse_timestamp(model.timestamp, "system metrics: timestamp"),
        cpu_count=model.cpu_count,
        cpu_utilization=_cpu_utilization(model.cpu_utilization),
        memory_usage_bytes=model.memory_usage_bytes,
        memory_total_bytes=model.memory_total_bytes,
        disk_usage_bytes=model.disk_usage_bytes,
        disk_total_bytes=model.disk_total_bytes,
    )


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


def expect_status_response(value: TransportPayload) -> GitStatus:
    # Checked before the generated model, which iterates the field first.
    _require_list_field(value, "fileStatus", "git status response")
    model = _expect_model_from_dict(
        value, StatusResponseModel, "git status response", StatusResponseModel.from_dict
    )
    return GitStatus(
        current_branch=model.current_branch,
        ahead=model.ahead,
        behind=model.behind,
        branch_published=model.branch_published,
        file_status=[
            GitFileStatus(name=item.name, staging=item.staging, worktree=item.worktree, extra=item.extra)
            for item in model.file_status
        ],
    )


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
    if execution_timeout is not None and not above_timer_maximum(
        checked_seconds(execution_timeout, "execution_timeout")
    ):
        execution_timeout_ms = timeout_millis_from_seconds(execution_timeout, "execution_timeout")
    # Above the timer maximum the process has no deadline (timeoutMs omitted).
    body: JsonObject = {"kind": kind}
    if command is not None:
        body["shellCommand"] = command
    if argv is not None:
        # A Sequence of strings, but not a str / bytes (a Sequence too), a
        # mapping or a one-shot iterable, refused before any request.
        if (
            not isinstance(argv, collections.abc.Sequence)
            or isinstance(argv, (str, bytes))
            or not argv
            or not all(isinstance(item, str) for item in argv)
        ):
            raise create_error("argv must be a non-empty sequence of strings", 400, "BAD_REQUEST", "SDK")
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
        if not isinstance(terminal, TerminalSize):
            raise create_error("terminal must be a TerminalSize", 400, "BAD_REQUEST", "SDK")
        optional["terminal"] = {
            "cols": terminal_dimension(terminal.cols, "cols"),
            "rows": terminal_dimension(terminal.rows, "rows"),
        }
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


def bare_signal(signal: Optional[str]) -> Optional[str]:
    """The SDK spells signals without the wire's ``SIG`` prefix."""
    if signal is not None and signal.startswith("SIG"):
        return signal[3:]
    return signal


def parse_process(payload: ProcessDto) -> ProcessInfo:
    # An id the SDK builds paths and identities from: empty is malformed.
    if not payload.id:
        raise create_error("expected process response: id is required", 400, "BAD_REQUEST", "SDK")
    raw_kind = enum_str(payload.kind)
    raw_state = enum_str(payload.state)
    raw_stdin = enum_str(payload.stdin)
    raw_keep_logs = enum_str(payload.keep_logs)
    raw_reason = enum_str(payload.reason) if payload.reason is not None else None
    return ProcessInfo(
        id=payload.id,
        kind=_process_info_kind_or_unknown(raw_kind),
        raw_kind=raw_kind,
        state=_process_state_or_unknown(raw_state),
        raw_state=raw_state,
        cwd=payload.cwd,
        stdin=_process_stdin_mode_or_unknown(raw_stdin),
        raw_stdin=raw_stdin,
        keep_logs=_process_keep_logs_or_unknown(raw_keep_logs),
        raw_keep_logs=raw_keep_logs,
        created_at=parse_timestamp(payload.created_at, "process response: createdAt"),
        system=payload.system is True,
        name=payload.name,
        pid=payload.pid,
        session_id=payload.session_id,
        shell_command=payload.shell_command,
        argv=payload.argv,
        code=payload.code,
        language=payload.language,
        shell=payload.shell,
        resolved_shell=payload.resolved_shell,
        login=payload.login,
        user=payload.user,
        execution_timeout=timeout_seconds_from_millis(_exact_timeout_ms(payload.timeout_ms)),
        terminal=_parse_terminal(payload.terminal),
        exit_code=payload.exit_code,
        signal=bare_signal(payload.signal),
        reason=_wait_reason_or_unknown(raw_reason) if raw_reason is not None else None,
        raw_reason=raw_reason,
        started_at=parse_optional_timestamp(payload.started_at, "process response: startedAt"),
        exited_at=parse_optional_timestamp(payload.exited_at, "process response: exitedAt"),
        truncated_head=payload.truncated_head is True,
        first_available_cursor=payload.first_available_cursor,
        last_cursor=payload.last_cursor,
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


def parse_process_page(payload: ProcessPagePayload) -> ListResult[ProcessInfo]:
    """A ``ProcessListResponse``: ``items`` is required; anything else is the
    malformed-response error."""
    if not isinstance(payload, ProcessListResponse):
        _require_list_field(payload, "items", "process list response")
        payload = expect_process_list_response(payload)
    return ListResult(items=[parse_process(item) for item in payload.items], next_cursor=None)


# The longest duration that is still a bound, in seconds (about 24.8 days):
# the TypeScript SDK's timers cannot arm a longer one, so both SDKs read a
# longer caller budget as no limit, like the client's request timeout.
MAX_BOUNDED_TIMEOUT_SECONDS = 2_147_483.647

# The largest millisecond value the SDKs put on the wire: the largest integer
# JavaScript represents exactly, so both SDKs refuse the same durations.
MAX_WIRE_MILLISECONDS = 2**53 - 1


def checked_seconds(value: object, name: str = "timeout") -> float | int:
    """A finite, non-negative number of seconds, or the SDK's ``BadRequestError``.

    An integer is checked without converting it to float, so one too large
    for a float is still accepted (and then capped by the caller), never an
    ``OverflowError``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise create_error(f"{name} must be a non-negative finite number", 400, "BAD_REQUEST", "SDK")
    # Narrowed to int | float from here on.
    if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
        raise create_error(f"{name} must be a non-negative finite number", 400, "BAD_REQUEST", "SDK")
    return value


def timeout_millis_from_seconds(timeout: Optional[float], name: str = "timeout") -> Optional[int]:
    """Whole wire milliseconds for a duration in seconds, rounded up: a
    positive duration is at least 1 ms, so it never reads as ``0``. A duration
    too large to send in milliseconds is the SDK's ``BadRequestError``."""
    if timeout is None:
        return None
    seconds = checked_seconds(timeout, name)
    if seconds == 0:
        return 0
    # The largest wire value both SDKs send exactly (a JavaScript safe integer).
    if seconds * 1000 > MAX_WIRE_MILLISECONDS:
        raise create_error(f"{name} is too large", 400, "BAD_REQUEST", "SDK")
    if isinstance(seconds, int):
        return seconds * 1000
    return max(1, math.ceil(round(seconds * 1000, 6)))


def above_timer_maximum(seconds: float) -> bool:
    """Whether a (checked) duration exceeds the longest bound both SDKs keep:
    such a caller budget or deadline means no limit."""
    return seconds > MAX_BOUNDED_TIMEOUT_SECONDS


def wait_budget(timeout: Optional[float]) -> Optional[float]:
    """A caller wait budget in seconds, validated: ``None`` and ``0`` keep their
    meaning, a budget above :data:`MAX_BOUNDED_TIMEOUT_SECONDS` is no limit (``0``)."""
    if timeout is None:
        return None
    seconds = checked_seconds(timeout)
    return 0 if above_timer_maximum(seconds) else timeout


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


def _exact_timeout_ms(value: Optional[int]) -> Optional[int]:
    """A process record's ``timeoutMs``: converted to seconds, so it must be
    an integer a double carries exactly (up to 2^53 - 1), as TypeScript reads
    it; anything larger is the malformed-response error."""
    if value is not None and not 0 <= value <= 2**53 - 1:
        raise create_error(
            "expected process response: timeoutMs must be a non-negative safe integer",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return value


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
        f"/processes/{toolbox_path_id(process_id, 'process id')}/wait",
        params=params,
        timeout=bounded,
        unbounded_read=bounded is None,
    )


def parse_wait(payload: WaitPayload) -> WaitResult:
    raw_reason = enum_str(payload.reason) if payload.reason is not None else None
    return WaitResult(
        reason=_wait_reason_or_unknown(raw_reason) if raw_reason is not None else "unknown",
        exit_code=payload.exit_code,
        signal=bare_signal(payload.signal),
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
    headers: Dict[str, str] = {}
    if follow:
        params["follow"] = "true"
        # The same headers the TypeScript SDK sends on a followed log.
        headers["Accept"] = "text/event-stream"
        if cursor:
            headers["Last-Event-ID"] = cursor
    return RequestSpec(
        "GET",
        f"/processes/{toolbox_path_id(process_id, 'process id')}/logs",
        params=params,
        timeout=None,
        headers=headers,
    )


def _require_list_field(payload: object, field: str, description: str) -> None:
    """A present list field that is not a list, named before the generated
    model iterates it (its own error would not name the field)."""
    if isinstance(payload, dict) and field in payload and not isinstance(payload[field], list):
        raise create_error(f"expected {description}: {field} must be a list", 400, "BAD_REQUEST", "SDK")


def parse_log_page(payload: ProcessLogPayload) -> LogPage:
    """A ``ProcessLogsResponse`` page, checked strictly: ``frames``,
    ``nextCursor``, ``truncatedHead`` and ``eof`` are required, with their
    types; anything else is the malformed-response error."""
    _require_list_field(payload, "frames", "process log page")
    page = payload if isinstance(payload, ProcessLogsResponse) else expect_process_log_page(payload)
    return LogPage(
        items=[_parse_log_frame(item) for item in page.frames],
        next_cursor=page.next_cursor,
        truncated_head=page.truncated_head,
        eof=page.eof,
        first_available_cursor=page.first_available_cursor,
    )


def parse_log_frame(payload: TransportPayload) -> LogFrame:
    """One ``LogFrameDto``, checked strictly."""
    return _parse_log_frame(expect_log_frame_dto(payload))


class SseParser:
    """Push-style SSE frame parser: feed lines, collect events.

    Sans-I/O so one parser serves both a sync and an async line source.
    """

    def __init__(self) -> None:
        self._event = "message"
        self._id: Optional[str] = None
        self._data_parts: List[str] = []
        # The event-stream format drops one U+FEFF at the very start of the
        # stream (TypeScript's TextDecoder does the same); elsewhere it is data.
        self._at_stream_start = True

    def feed(self, raw: str) -> Optional[Tuple[str, JsonObject]]:
        message = self.feed_message(raw)
        return None if message is None else (message[0], expect_object(loads_json(message[1], "SSE event")))

    def feed_message(self, raw: str) -> Optional[Tuple[str, str, Optional[str]]]:
        """A complete message as ``(event, data, id)``: the raw ``data:`` text,
        unparsed, so a consumer parses only the events it knows, and the
        message's ``id:`` line (``None`` without one)."""
        line = raw.rstrip("\r\n")
        if self._at_stream_start:
            self._at_stream_start = False
            line = line.removeprefix("\ufeff")
        if not line:
            if not self._data_parts:
                self._event = "message"
                self._id = None
                return None
            message = (self._event, "\n".join(self._data_parts), self._id)
            self._event = "message"
            self._id = None
            self._data_parts = []
            return message
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._event = value
        elif name == "data":
            self._data_parts.append(value)
        elif name == "id":
            self._id = value
        return None


def parse_sse_lines(lines: Iterable[str]) -> Iterator[Tuple[str, JsonObject]]:
    parser = SseParser()
    for raw in lines:
        event = parser.feed(raw)
        if event is not None:
            yield event


def sse_json(event: str, data: str, description: str) -> JsonObject:
    """The JSON object a known SSE event carries (empty data reads as ``{}``);
    anything else is the malformed-response error naming the event."""
    if not data:
        return {}
    try:
        payload = loads_json(data, description)
    except ValueError:
        raise create_error(
            f"expected {description}: the data is not JSON", 400, "BAD_REQUEST", "SDK"
        ) from None
    if not isinstance(payload, dict):
        raise create_error(
            f"expected {description}: the data is not a JSON object", 400, "BAD_REQUEST", "SDK"
        )
    return payload


async def parse_sse_messages_async(
    lines: AsyncIterable[str],
) -> AsyncIterator[Tuple[str, str, Optional[str]]]:
    """SSE messages as ``(event, data, id)``, the data unparsed (see :func:`sse_json`)."""
    parser = SseParser()
    async for raw in lines:
        message = parser.feed_message(raw)
        if message is not None:
            yield message


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
    return RequestSpec(
        "POST", f"/processes/{toolbox_path_id(process_id, 'process id')}/stdin", json_body=body
    )


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
    return RequestSpec(
        "POST", f"/processes/{toolbox_path_id(process_id, 'process id')}/signals", json_body=body
    )


_MAX_INT32: Final = 2**31 - 1


def terminal_dimension(value: object, name: str) -> int:
    """A terminal's ``cols`` / ``rows``: an int32 of at least 1, as the
    contract states."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_INT32:
        raise create_error(f"{name} must be an integer between 1 and {_MAX_INT32}", 400, "BAD_REQUEST", "SDK")
    return value


def resize_spec(process_id: str, *, cols: int, rows: int) -> RequestSpec:
    cols = terminal_dimension(cols, "cols")
    rows = terminal_dimension(rows, "rows")
    return RequestSpec(
        "POST",
        f"/processes/{toolbox_path_id(process_id, 'process id')}/resize",
        json_body={"cols": cols, "rows": rows},
    )


def delete_process_spec(process_id: str) -> RequestSpec:
    return RequestSpec("DELETE", f"/processes/{toolbox_path_id(process_id, 'process id')}")


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
    """A ``BulkUploadResponse``: ``files`` (each with ``path`` and ``bytes``)
    and ``errors`` are required; anything else is the malformed-response error."""
    _require_list_field(payload, "files", "bulk upload response")
    model = _expect_model_from_dict(
        payload, BulkUploadResponse, "bulk upload response", BulkUploadResponse.from_dict
    )
    return BulkWriteResult(
        files=[BulkWriteFile(path=item.path, bytes=item.bytes) for item in model.files],
        errors=list(model.errors),
    )


def parse_file_match(payload: JsonValue) -> FileMatch:
    model = _expect_model_from_dict(payload, MatchModel, "grep match", MatchModel.from_dict)
    return FileMatch(file=model.file, line=model.line, content=model.content)


def parse_replace_result(payload: JsonValue) -> ReplaceResult:
    model = _expect_model_from_dict(
        payload, ReplaceResultModel, "replace result", ReplaceResultModel.from_dict
    )
    return ReplaceResult(file=model.file, success=model.success, error=model.error)


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
            errors.append(_bulk_read_error(body))
    return BulkReadResult(files=files, errors=errors)


def _bulk_read_error(body: bytes) -> BulkReadError:
    """One bulk-download error part: a JSON object with string ``path`` and
    ``message`` (``code`` / ``source`` strings when present)."""
    description = "bulk download error part"
    try:
        payload = loads_json(body.decode("utf-8"), description)
    except (UnicodeDecodeError, ValueError):
        raise create_error(
            f"expected {description}: the data is not JSON", 400, "BAD_REQUEST", "SDK"
        ) from None
    if not isinstance(payload, dict):
        raise create_error(f"expected {description}", 400, "BAD_REQUEST", "SDK")
    for name in ("path", "message"):
        if not isinstance(payload.get(name), str):
            raise create_error(f"expected {description}: {name} must be a string", 400, "BAD_REQUEST", "SDK")
    for name in ("code", "source"):
        if payload.get(name) is not None and not isinstance(payload.get(name), str):
            raise create_error(f"expected {description}: {name} must be a string", 400, "BAD_REQUEST", "SDK")
    return BulkReadError(
        path=payload["path"],
        message=payload["message"],
        code=_optional_str(payload.get("code")),
        source=_optional_str(payload.get("source")),
    )


def list_shape(
    payload: TransportPayload, *, description: str = "list response"
) -> Tuple[List[JsonObject], Optional[str]]:
    """The items of a list response: a bare array or an envelope whose
    ``items`` is an array; anything else is malformed."""
    if isinstance(payload, list):
        items: JsonValue = payload
        cursor_holder: JsonObject = {}
    else:
        cursor_holder = expect_object(payload)
        items = cursor_holder.get("items")
        if not isinstance(items, list):
            problem = "is required" if items is None else "must be a list"
            raise create_error(f"expected {description}: items {problem}", 400, "BAD_REQUEST", "SDK")
    obj = cursor_holder
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise create_error(
                f"expected {description}: items[{index}] must be an object", 400, "BAD_REQUEST", "SDK"
            )
    # A present cursor of the wrong type would silently end pagination.
    cursor = obj.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise create_error(f"expected {description}: nextCursor must be a string", 400, "BAD_REQUEST", "SDK")
    return [expect_object(item) for item in items], cursor or None


_SEARCH_LIST_FIELDS = ("status", "sandbox_class", "region")
_SEARCH_TIMESTAMP_FIELDS = (
    "created_start_at",
    "created_end_at",
    "updated_start_at",
    "updated_end_at",
    "last_activity_start_at",
    "last_activity_end_at",
)
_SEARCH_WIRE_NAMES = {"sandbox_class": "class", "region": "region_ids"}


def search_filters_present(filters: SandboxSearchFilters) -> bool:
    """True when any ``GET /sandboxes/search``-only filter is set (the ``list()`` routing rule)."""
    return any(getattr(filters, f.name) is not None for f in fields(SandboxSearchFilters))


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


def search_params(query: SearchSandboxesQuery) -> Dict[str, JsonValue]:
    """Wire query for ``GET /sandboxes/search``: lists comma-joined, labels JSON, timestamps RFC 3339."""
    params: Dict[str, JsonValue] = {}
    for f in fields(query):
        value = getattr(query, f.name)
        if value is None:
            continue
        wire = _SEARCH_WIRE_NAMES.get(f.name, f.name)
        if f.name in _SEARCH_LIST_FIELDS:
            params[wire] = _comma_list(f.name, value)
        elif f.name == "labels":
            if not isinstance(value, Mapping):
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
) -> Tuple[List[JsonObject], Optional[str], int, SearchTotalRelation, str]:
    """Split a ``GET /sandboxes/search`` body into items, cursor, total, and relation.

    The search contract is an object with a
    ``sandboxes`` array: a body that is not an object, or whose ``sandboxes`` is missing or
    not an array, is the SDK's own :class:`BadRequestError` rather than an
    empty page — an ``items`` array is NOT accepted as a stand-in. Unknown
    extra fields (top-level and per item) are tolerated as everywhere else.
    """
    obj = expect_object(payload)
    raw_sandboxes = obj.get("sandboxes")
    if not isinstance(raw_sandboxes, list):
        raise create_error("search response is missing the sandboxes array", 400, "BAD_REQUEST", "SDK")
    items = [expect_object(item) for item in raw_sandboxes]
    cursor = obj.get("next_cursor")
    next_cursor = cursor if isinstance(cursor, str) else None
    total_raw = obj.get("total")
    total = int(total_raw) if isinstance(total_raw, (int, float)) and not isinstance(total_raw, bool) else 0
    relation_raw = obj.get("total_relation")
    raw_total_relation = relation_raw if isinstance(relation_raw, str) else "eq"
    total_relation: SearchTotalRelation = (
        raw_total_relation if raw_total_relation == "eq" or raw_total_relation == "gte" else "unknown"
    )
    return items, next_cursor, total, total_relation, raw_total_relation


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
            return {"type": "registry", "ref": ref}
        case {"type": "dockerfile", "context_object": str(context_object), "dockerfile": str(dockerfile)}:
            return {
                "type": "dockerfile",
                "context_object": context_object,
                "dockerfile": dockerfile,
            }
        case {"type": "named", "name": str(name)}:
            return {"type": "named", "name": name}
        case {"type": "manifest", "name": str(name)}:
            return {"type": "manifest", "name": name}
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
        var_class=str(data.get("class") or data.get("var_class") or ""),
        available_in=_optional_str_list(data.get("region_ids")) or [],
        origin=str(data["origin"]),
        created_at=_isoformat_or_none(data.get("created_at")),
        updated_at=_isoformat_or_none(data.get("updated_at")),
        error=_optional_str(data.get("error")),
        expires_at=_isoformat_or_none(data.get("expires_at")),
        source=_image_source(data.get("source")),
        bytes=_optional_int(data.get("bytes")),
        layer_count=_optional_int(data.get("layer_count")),
        format_version=_optional_int(data.get("format_version")),
        format_outdated=data.get("format_outdated") is True,
        cursor=_optional_str(data.get("cursor")),
        platform=data.get("platform") is True,
        default=data.get("default") is True,
        deprecated=data.get("deprecated") is True,
        disk_mib=_optional_int(data.get("disk_mib")),
        raw_retention=raw_retention,
    )


def parse_snapshot(value: object) -> Snapshot:
    data = value.to_dict() if hasattr(value, "to_dict") and callable(value.to_dict) else expect_object(value)
    raw_kind = _wire_string(data.get("kind"))
    return Snapshot(
        id=str(data["id"]),
        name=str(data["name"]),
        status=str(data["status"]),
        region=str(data["region_id"]),
        kind=_snapshot_kind(raw_kind),
        var_class=_wire_string(data.get("class") or data.get("var_class")),
        available_in=_optional_str_list(data.get("region_ids")) or [],
        created_at=_isoformat_or_none(data.get("created_at")),
        updated_at=_isoformat_or_none(data.get("updated_at")),
        error=_optional_str(data.get("error")),
        raw_kind=raw_kind,
        bytes=_optional_int(data.get("bytes")),
        memory_bytes=_optional_int(data.get("memory_bytes")),
        sandbox_id=_optional_str(data.get("sandbox_id")),
        cursor=_optional_str(data.get("cursor")),
    )


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


def _isoformat_or_none(value: JsonValue | object) -> str | None:
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat()).replace("+00:00", "Z")
    return None


def gb_to_mib(gb: float) -> int:
    return round(gb * 1024)


def mib_to_gb(mib: int) -> int:
    return max(1, (mib + 1023) // 1024)


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
        timestamp_ms=payload.timestamp,
    )


def _parse_terminal(value: ToolboxTerminalSize | None) -> Optional[TerminalSize]:
    """A process record's terminal: int32 dimensions of at least 1, as the
    request's (:func:`terminal_dimension`); anything else is malformed."""
    if value is None:
        return None
    for name, dimension in (("cols", value.cols), ("rows", value.rows)):
        if not 1 <= dimension <= _MAX_INT32:
            raise create_error(
                f"expected process response: terminal: {name} must be an integer between 1 and {_MAX_INT32}",
                400,
                "BAD_REQUEST",
                "SDK",
            )
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
        encoded = filename_star.group(1)
        # Bad percent-encoding or invalid UTF-8 is malformed, as in the TypeScript SDK.
        try:
            if re.search(r"%(?![0-9A-Fa-f]{2})", encoded):
                raise ValueError(encoded)
            return unquote(encoded, encoding="utf-8", errors="strict")
        except ValueError:
            raise create_error(
                "expected bulk download file part: filename* is not valid percent-encoded UTF-8",
                400,
                "BAD_REQUEST",
                "SDK",
            ) from None
    filename = _CONTENT_DISPOSITION_FILENAME_RE.search(header)
    return filename.group(1) if filename is not None else None
