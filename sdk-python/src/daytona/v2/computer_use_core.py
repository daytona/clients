# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from typing import Dict, Final, List, Mapping, Optional, Tuple

from ._core import (
    MAX_SAFE_INTEGER,
    JsonValue,
    TransportPayload,
    _expect_model_from_dict,
    enum_str,
    finite_number,
    int32_field,
    is_json_value,
)
from ._gen.toolbox import (
    ComputerUseA11yFindResponse,
    ComputerUseA11yTreeResponse,
    ComputerUseAccessibilityNode,
    ComputerUseDisplay,
    ComputerUseDisplayInfoResponse,
    ComputerUseDisplayWindowsResponse,
    ComputerUseLifecycleResponse,
    ComputerUsePoint,
    ComputerUseProcessErrorsResponse,
    ComputerUseProcessLogsResponse,
    ComputerUseProcessRestartResponse,
    ComputerUseProcessRunningStatus,
    ComputerUseProcessStatus,
    ComputerUseRecording,
    ComputerUseRecordingsResponse,
    ComputerUseRestartResponse,
    ComputerUseScreenshotResponse,
    ComputerUseScrollResponse,
    ComputerUseStatusResponse,
    ComputerUseWindow,
)
from ._gen.toolbox import ComputerUseDesktopFailure as ComputerUseDesktopFailureDto
from ._timestamps import parse_optional_timestamp, parse_timestamp
from .computer_use_types import (
    ComputerUseA11yFindResult,
    ComputerUseA11yTree,
    ComputerUseAccessibilityBounds,
    ComputerUseDesktopFailure,
    ComputerUseFailedStatus,
    ComputerUseLifecycleResult,
    ComputerUseNotStartedStatus,
    ComputerUseProcessErrors,
    ComputerUseProcessLogs,
    ComputerUseProcessRestartResult,
    ComputerUseReadyStatus,
    ComputerUseRecordingList,
    ComputerUseScreenshot,
    ComputerUseScrollResult,
    ComputerUseStartingStatus,
    ComputerUseUnknownStatus,
    _computer_use_desktop_failure_code_or_unknown,
)
from .computer_use_types import ComputerUseAccessibilityNode as ComputerUseAccessibilityNodeInfo
from .computer_use_types import ComputerUseDisplay as ComputerUseDisplayInfo
from .computer_use_types import (
    ComputerUsePoint as ComputerUsePointInfo,
)
from .computer_use_types import ComputerUseProcessRunningStatus as ComputerUseProcessRunningStatusInfo
from .computer_use_types import ComputerUseProcessStatus as ComputerUseProcessStatusInfo
from .computer_use_types import ComputerUseRecording as ComputerUseRecordingInfo
from .computer_use_types import (
    ComputerUseRestartResponse as ComputerUseRestartResponseInfo,
)
from .computer_use_types import (
    ComputerUseStatusResponse as ComputerUseStatusResponseInfo,
)
from .computer_use_types import (
    ComputerUseWindow as ComputerUseWindowInfo,
)
from .errors import DaytonaError, create_error

ComputerUseProcessStatusMap = dict[str, ComputerUseProcessStatus]


def computer_use_params(**kwargs: JsonValue) -> Dict[str, JsonValue]:
    return {key: value for key, value in kwargs.items() if value is not None}


# The deepest accessibility tree the SDK reads: tree() asks the sandbox for at
# most this many levels below the root (maxDepth) unless the caller asks for
# fewer, and a response nested deeper is malformed. Raising it later is
# additive.
A11Y_MAX_DEPTH: Final = 256


def a11y_max_depth(max_depth: object) -> int:
    """A caller's ``max_depth``: an integer in 0..A11Y_MAX_DEPTH (``None``: the cap)."""
    if max_depth is None:
        return A11Y_MAX_DEPTH
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= A11Y_MAX_DEPTH:
        raise create_error(
            f"max_depth must be an integer between 0 and {A11Y_MAX_DEPTH}", 400, "BAD_REQUEST", "SDK"
        )
    return max_depth


def _childless(node: JsonValue) -> JsonValue:
    """A node without its children, so a generated model validates one level only."""
    if isinstance(node, dict) and "children" in node:
        return {key: value for key, value in node.items() if key != "children"}
    return node


def parse_computer_use_a11y_tree_payload(value: TransportPayload, *, max_depth: int) -> ComputerUseA11yTree:
    """An accessibility tree response, read without recursion: the wrapper and
    each node are validated one level at a time, and a node deeper than
    ``max_depth`` levels below the root is the malformed-response error."""
    description = "computer-use accessibility tree response"
    if not isinstance(value, dict):
        raise create_error(f"expected {description}", 400, "BAD_REQUEST", "SDK")
    wrapper = _expect_model_from_dict(
        {**value, "root": _childless(value.get("root"))},
        ComputerUseA11yTreeResponse,
        description,
        ComputerUseA11yTreeResponse.from_dict,
    )
    root = _parse_a11y_nodes([value["root"]], max_depth, description)[0]
    return ComputerUseA11yTree(root=root, truncated=wrapper.truncated)


def parse_computer_use_a11y_find_payload(value: TransportPayload) -> ComputerUseA11yFindResult:
    """An accessibility find response, read without recursion; a match nested
    deeper than :data:`A11Y_MAX_DEPTH` levels is malformed."""
    description = "computer-use accessibility find response"
    if not isinstance(value, dict):
        raise create_error(f"expected {description}", 400, "BAD_REQUEST", "SDK")
    matches = value.get("matches")
    wrapper = _expect_model_from_dict(
        {
            **value,
            "matches": [_childless(item) for item in matches] if isinstance(matches, list) else matches,
        },
        ComputerUseA11yFindResponse,
        description,
        ComputerUseA11yFindResponse.from_dict,
    )
    raw_matches: List[JsonValue] = matches if isinstance(matches, list) else []
    return ComputerUseA11yFindResult(
        matches=_parse_a11y_nodes(raw_matches, A11Y_MAX_DEPTH, description), truncated=wrapper.truncated
    )


def _parse_a11y_nodes(
    roots: List[JsonValue], max_depth: int, description: str
) -> List[ComputerUseAccessibilityNodeInfo]:
    """Map node trees with an explicit stack (each node's fields are read when
    it is reached, its children appended to its list afterwards), so no step
    recurses whatever the response's depth. A node more than ``max_depth``
    levels below its root is the malformed-response error."""
    results: List[ComputerUseAccessibilityNodeInfo] = []
    stack: List[Tuple[JsonValue, int, List[ComputerUseAccessibilityNodeInfo]]] = [
        (node, 0, results) for node in reversed(roots)
    ]
    while stack:
        raw, depth, into = stack.pop()
        if depth > max_depth:
            raise create_error(
                f"expected {description}: a node is nested deeper than {max_depth} levels",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        model = _expect_model_from_dict(
            _childless(raw),
            ComputerUseAccessibilityNode,
            f"{description}: node",
            ComputerUseAccessibilityNode.from_dict,
        )
        children = raw.get("children") if isinstance(raw, dict) else None
        if children is not None and not isinstance(children, list):
            raise create_error(f"expected {description}: children must be a list", 400, "BAD_REQUEST", "SDK")
        built: Optional[List[ComputerUseAccessibilityNodeInfo]] = None if children is None else []
        into.append(_a11y_node_info(model, built))
        if built is not None and children:
            stack.extend((child, depth + 1, built) for child in reversed(children))
    return results


def expect_computer_use_display_info_response(value: TransportPayload) -> ComputerUseDisplayInfoResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseDisplayInfoResponse,
        "computer-use display info response",
        ComputerUseDisplayInfoResponse.from_dict,
    )


def expect_computer_use_display_windows_response(
    value: TransportPayload,
) -> ComputerUseDisplayWindowsResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseDisplayWindowsResponse,
        "computer-use display windows response",
        ComputerUseDisplayWindowsResponse.from_dict,
    )


def expect_computer_use_lifecycle_response(value: TransportPayload) -> ComputerUseLifecycleResponse:
    # Checked before the generated model, which calls .items() on the map first.
    if isinstance(value, dict) and "status" in value:
        status = value["status"]
        if not isinstance(status, dict) or not all(isinstance(entry, dict) for entry in status.values()):
            raise create_error(
                "expected computer-use lifecycle response: status must be a map of process statuses",
                400,
                "BAD_REQUEST",
                "SDK",
            )
    return _expect_model_from_dict(
        value,
        ComputerUseLifecycleResponse,
        "computer-use lifecycle response",
        ComputerUseLifecycleResponse.from_dict,
    )


def expect_computer_use_point(value: TransportPayload) -> ComputerUsePoint:
    return _expect_model_from_dict(
        value, ComputerUsePoint, "computer-use point response", ComputerUsePoint.from_dict
    )


def expect_computer_use_process_errors_response(value: TransportPayload) -> ComputerUseProcessErrorsResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseProcessErrorsResponse,
        "computer-use process errors response",
        ComputerUseProcessErrorsResponse.from_dict,
    )


def expect_computer_use_process_logs_response(value: TransportPayload) -> ComputerUseProcessLogsResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseProcessLogsResponse,
        "computer-use process logs response",
        ComputerUseProcessLogsResponse.from_dict,
    )


def expect_computer_use_process_restart_response(
    value: TransportPayload,
) -> ComputerUseProcessRestartResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseProcessRestartResponse,
        "computer-use process restart response",
        ComputerUseProcessRestartResponse.from_dict,
    )


def expect_computer_use_process_running_status(value: TransportPayload) -> ComputerUseProcessRunningStatus:
    return _expect_model_from_dict(
        value,
        ComputerUseProcessRunningStatus,
        "computer-use process running status",
        ComputerUseProcessRunningStatus.from_dict,
    )


def expect_computer_use_process_status_map(value: TransportPayload) -> ComputerUseProcessStatusMap:
    if not isinstance(value, dict):
        raise create_error("expected computer-use process status map", 400, "BAD_REQUEST", "SDK")
    result: ComputerUseProcessStatusMap = {}
    for name, item in value.items():
        if not isinstance(name, str):
            raise create_error("expected computer-use process status map", 400, "BAD_REQUEST", "SDK")
        result[name] = _expect_model_from_dict(
            item,
            ComputerUseProcessStatus,
            "computer-use process status entry",
            ComputerUseProcessStatus.from_dict,
        )
    return result


def expect_computer_use_recording(value: TransportPayload) -> ComputerUseRecording:
    return _expect_model_from_dict(
        value,
        ComputerUseRecording,
        "computer-use recording response",
        ComputerUseRecording.from_dict,
    )


def expect_computer_use_recordings_response(value: TransportPayload) -> ComputerUseRecordingsResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseRecordingsResponse,
        "computer-use recordings response",
        ComputerUseRecordingsResponse.from_dict,
    )


def expect_computer_use_screenshot_response(value: TransportPayload) -> ComputerUseScreenshotResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseScreenshotResponse,
        "computer-use screenshot response",
        ComputerUseScreenshotResponse.from_dict,
    )


def expect_computer_use_scroll_response(value: TransportPayload) -> ComputerUseScrollResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseScrollResponse,
        "computer-use scroll response",
        ComputerUseScrollResponse.from_dict,
    )


def expect_computer_use_status_response(value: TransportPayload) -> ComputerUseStatusResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseStatusResponse,
        "computer-use status response",
        ComputerUseStatusResponse.from_dict,
    )


def expect_computer_use_restart_response(value: TransportPayload) -> ComputerUseRestartResponse:
    return _expect_model_from_dict(
        value,
        ComputerUseRestartResponse,
        "computer-use restart response",
        ComputerUseRestartResponse.from_dict,
    )


_INVALID_STATUS: Final = "Invalid computer-use status response"
_INVALID_RESTART: Final = "Invalid computer-use restart response"


def _invalid(prefix: str, detail: str) -> DaytonaError:
    """A malformed status or restart response, naming the field (the
    TypeScript SDK's messages for the same body)."""
    return create_error(f"{prefix}: {detail}", 400, "BAD_REQUEST", "SDK")


def _check_status_fields(payload: object) -> Mapping[str, object]:
    """The raw fields the status parser reads, each checked by name."""
    if not isinstance(payload, Mapping):
        raise _invalid(_INVALID_STATUS, "the body must be an object")
    for field in ("status", "desktop"):
        if not isinstance(payload.get(field), str):
            raise _invalid(_INVALID_STATUS, f"{field} must be a string")
    cause = payload.get("cause")
    if cause is not None:
        if not isinstance(cause, Mapping):
            raise _invalid(_INVALID_STATUS, "cause must be null or an object")
        for field in ("code", "message"):
            if not isinstance(cause.get(field), str):
                raise _invalid(_INVALID_STATUS, f"cause.{field} must be a string")
    return payload


def _status_model(payload: object) -> ComputerUseStatusResponse:
    if isinstance(payload, ComputerUseStatusResponse):
        return payload
    fields = _check_status_fields(payload)
    try:
        return expect_computer_use_status_response(dict(fields))
    except DaytonaError as exc:
        raise _invalid(_INVALID_STATUS, exc.message) from exc


def parse_computer_use_status(
    payload: ComputerUseStatusResponse | Mapping[str, JsonValue] | object,
) -> ComputerUseStatusResponseInfo:
    model = _status_model(payload)
    status = enum_str(model.status)
    desktop = enum_str(model.desktop)
    extra_fields = _additional_json_fields(model.additional_properties)
    # Outside the failed state the contract allows `cause` to be omitted or null.
    no_cause = model.cause is None

    if status == "inactive" and desktop == "not_started":
        if no_cause:
            return ComputerUseNotStartedStatus(extra_fields=extra_fields)
    elif status == "inactive" and desktop == "starting":
        if no_cause:
            return ComputerUseStartingStatus(extra_fields=extra_fields)
    elif status == "active" and desktop == "ready":
        if no_cause:
            return ComputerUseReadyStatus(extra_fields=extra_fields)
    elif status == "inactive" and desktop == "failed":
        if model.cause is not None:
            return ComputerUseFailedStatus(
                cause=_parse_computer_use_desktop_failure(model.cause),
                extra_fields=extra_fields,
            )
        raise _invalid(_INVALID_STATUS, "cause is required when status is 'inactive' and desktop is 'failed'")
    else:
        # A pair this SDK release does not list. Reading it as "unknown" keeps the raw values
        # and lets a deployed client survive a vocabulary the server grew.
        return ComputerUseUnknownStatus(
            raw_status=status,
            raw_desktop=desktop,
            cause=_parse_computer_use_desktop_failure(model.cause) if model.cause is not None else None,
            extra_fields=extra_fields,
        )
    raise _invalid(
        _INVALID_STATUS, f"cause must be absent when status is '{status}' and desktop is '{desktop}'"
    )


def parse_computer_use_restart(
    payload: ComputerUseRestartResponse | Mapping[str, JsonValue] | object,
) -> ComputerUseRestartResponseInfo:
    if isinstance(payload, ComputerUseRestartResponse):
        model = payload
    else:
        if not isinstance(payload, Mapping):
            raise _invalid(_INVALID_RESTART, "the body must be an object")
        if not isinstance(payload.get("message"), str):
            raise _invalid(_INVALID_RESTART, "message must be a string")
        if payload.get("status") != "active":
            raise _invalid(_INVALID_RESTART, "status must be 'active'")
        if payload.get("desktop") != "ready":
            raise _invalid(_INVALID_RESTART, "desktop must be 'ready'")
        try:
            model = expect_computer_use_restart_response(dict(payload))
        except DaytonaError as exc:
            raise _invalid(_INVALID_RESTART, exc.message) from exc
    if enum_str(model.status) != "active":
        raise _invalid(_INVALID_RESTART, "status must be 'active'")
    if enum_str(model.desktop) != "ready":
        raise _invalid(_INVALID_RESTART, "desktop must be 'ready'")
    return ComputerUseRestartResponseInfo(
        message=model.message,
        extra_fields=_additional_json_fields(model.additional_properties),
    )


def _parse_computer_use_desktop_failure(payload: ComputerUseDesktopFailureDto) -> ComputerUseDesktopFailure:
    raw_code = enum_str(payload.code)
    return ComputerUseDesktopFailure(
        code=_computer_use_desktop_failure_code_or_unknown(raw_code),
        message=payload.message,
        extra_fields=_additional_json_fields(payload.additional_properties),
        raw_code=raw_code,
    )


def _additional_json_fields(
    payload: Mapping[str, JsonValue], known: set[str] | None = None
) -> Dict[str, JsonValue] | None:
    extra: Dict[str, JsonValue] = {}
    for key, value in payload.items():
        if known is not None and key in known:
            continue
        if is_json_value(value):
            extra[key] = value
    return extra or None


# The contract's RestartBackoffMs is an int64 (minimum 0); both SDKs read it up
# to the largest integer JavaScript represents exactly.


def _restart_backoff_seconds(milliseconds: int) -> float:
    if not 0 <= milliseconds <= MAX_SAFE_INTEGER:
        raise create_error(
            "expected computer-use process status: RestartBackoffMs must be a non-negative safe integer",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return milliseconds / 1000


def parse_computer_use_process_status_map(
    payload: ComputerUseProcessStatusMap,
) -> Dict[str, ComputerUseProcessStatusInfo]:
    result: Dict[str, ComputerUseProcessStatusInfo] = {}
    for name, raw in payload.items():
        extra_fields = _additional_json_fields(raw.additional_properties)
        d = "computer-use process status"
        result[name] = ComputerUseProcessStatusInfo(
            running=raw.running,
            priority=_i32(raw.priority, "Priority", d),
            auto_restart=raw.auto_restart,
            pid=int32_field(raw.pid, "Pid", d),
            failed=raw.failed,
            restart_attempts=_i32(raw.restart_attempts, "RestartAttempts", d, non_negative=True),
            restart_backoff=_restart_backoff_seconds(raw.restart_backoff_ms),
            status=raw.status,
            extra_fields={wire_key_to_sdk_key(key): value for key, value in extra_fields.items()}
            if extra_fields
            else None,
        )
    return result


def wire_key_to_sdk_key(key: str) -> str:
    return re.sub(r"^[A-Z]+(?=[A-Z][a-z]|$)|^[A-Z]", lambda match: match.group(0).lower(), key)


def parse_computer_use_lifecycle(payload: ComputerUseLifecycleResponse) -> ComputerUseLifecycleResult:
    return ComputerUseLifecycleResult(
        message=payload.message, status=parse_computer_use_process_status_map(payload.status)
    )


def parse_computer_use_process_running_status(
    payload: ComputerUseProcessRunningStatus,
) -> ComputerUseProcessRunningStatusInfo:
    return ComputerUseProcessRunningStatusInfo(process_name=payload.process_name, running=payload.running)


def parse_computer_use_process_restart(
    payload: ComputerUseProcessRestartResponse,
) -> ComputerUseProcessRestartResult:
    return ComputerUseProcessRestartResult(message=payload.message, process_name=payload.process_name)


def parse_computer_use_process_logs(payload: ComputerUseProcessLogsResponse) -> ComputerUseProcessLogs:
    return ComputerUseProcessLogs(process_name=payload.process_name, logs=payload.logs)


def parse_computer_use_process_errors(payload: ComputerUseProcessErrorsResponse) -> ComputerUseProcessErrors:
    return ComputerUseProcessErrors(process_name=payload.process_name, errors=payload.errors)


def _i32(value: int, field: str, description: str, *, non_negative: bool = False) -> int:
    """A required int32 field (see :func:`int32_field`)."""
    int32_field(value, field, description, non_negative=non_negative)
    return value


def parse_computer_use_point(
    payload: ComputerUsePoint, description: str = "computer-use point"
) -> ComputerUsePointInfo:
    return ComputerUsePointInfo(x=_i32(payload.x, "x", description), y=_i32(payload.y, "y", description))


def parse_computer_use_screenshot(payload: ComputerUseScreenshotResponse) -> ComputerUseScreenshot:
    cursor_position = (
        parse_computer_use_point(payload.cursor_position, "computer-use screenshot response: cursorPosition")
        if payload.cursor_position is not None
        else None
    )
    return ComputerUseScreenshot(
        screenshot=payload.screenshot,
        cursor_position=cursor_position,
        size_bytes=payload.size_bytes,
    )


def parse_computer_use_scroll(payload: ComputerUseScrollResponse) -> ComputerUseScrollResult:
    return ComputerUseScrollResult(success=payload.success)


def parse_computer_use_recording(payload: ComputerUseRecording) -> ComputerUseRecordingInfo:
    return ComputerUseRecordingInfo(
        id=payload.id,
        file_name=payload.file_name,
        file_path=payload.file_path,
        start_time=parse_timestamp(payload.start_time, "computer-use recording: startTime"),
        status=payload.status,
        end_time=parse_optional_timestamp(payload.end_time, "computer-use recording: endTime"),
        duration_seconds=_finite_or_none(payload.duration_seconds, "computer-use recording: durationSeconds"),
        size_bytes=payload.size_bytes,
    )


def _finite_or_none(value: Optional[float], description: str) -> Optional[float]:
    """A JSON number the decoder read as ``inf`` (``1e309``), or an integer
    a float cannot carry, is no number."""
    if value is not None and not finite_number(value):
        raise create_error(f"expected {description} must be a number", 400, "BAD_REQUEST", "SDK")
    return value


def parse_computer_use_recording_list(payload: ComputerUseRecordingsResponse) -> ComputerUseRecordingList:
    return ComputerUseRecordingList(
        recordings=[parse_computer_use_recording(item) for item in payload.recordings]
    )


def parse_computer_use_displays(payload: ComputerUseDisplayInfoResponse) -> list[ComputerUseDisplayInfo]:
    return [_parse_computer_use_display(display) for display in payload.displays]


def parse_computer_use_windows(payload: ComputerUseDisplayWindowsResponse) -> list[ComputerUseWindowInfo]:
    return [_parse_computer_use_window(window) for window in payload.windows]


def _parse_computer_use_display(payload: ComputerUseDisplay) -> ComputerUseDisplayInfo:
    d = "computer-use display"
    return ComputerUseDisplayInfo(
        id=payload.id,
        x=_i32(payload.x, "x", d),
        y=_i32(payload.y, "y", d),
        width=_i32(payload.width, "width", d, non_negative=True),
        height=_i32(payload.height, "height", d, non_negative=True),
        is_active=payload.is_active,
    )


def _parse_computer_use_window(payload: ComputerUseWindow) -> ComputerUseWindowInfo:
    return ComputerUseWindowInfo(
        id=payload.id,
        title=payload.title,
        x=_i32(payload.x, "x", "computer-use window"),
        y=_i32(payload.y, "y", "computer-use window"),
        width=_i32(payload.width, "width", "computer-use window", non_negative=True),
        height=_i32(payload.height, "height", "computer-use window", non_negative=True),
        is_active=payload.is_active,
    )


def _a11y_node_info(
    payload: ComputerUseAccessibilityNode, children: Optional[List[ComputerUseAccessibilityNodeInfo]]
) -> ComputerUseAccessibilityNodeInfo:
    bounds_payload = payload.bounds
    return ComputerUseAccessibilityNodeInfo(
        id=payload.id,
        role=payload.role,
        name=payload.name,
        description=payload.description,
        bounds=ComputerUseAccessibilityBounds(
            x=_i32(bounds_payload.x, "x", "computer-use accessibility node: bounds"),
            y=_i32(bounds_payload.y, "y", "computer-use accessibility node: bounds"),
            width=_i32(bounds_payload.width, "width", "computer-use accessibility node: bounds"),
            height=_i32(bounds_payload.height, "height", "computer-use accessibility node: bounds"),
        ),
        states=payload.states,
        actions=payload.actions,
        children=children,
        value=payload.value,
        value_truncated=bool(payload.value_truncated),
    )
