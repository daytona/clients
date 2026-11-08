# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import re
from typing import Dict, Final, List, Mapping, Optional, Tuple

from ._core import (
    JsonValue,
    TransportPayload,
    _expect_model_from_dict,
    enum_str,
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


def _status_model(
    payload: ComputerUseStatusResponse | Mapping[str, JsonValue],
) -> ComputerUseStatusResponse:
    if isinstance(payload, ComputerUseStatusResponse):
        return payload
    try:
        return expect_computer_use_status_response(dict(payload))
    except DaytonaError as exc:
        raise create_error(
            f"Invalid computer-use status response: {exc.message}",
            400,
            "BAD_REQUEST",
            "SDK",
        ) from exc


def parse_computer_use_status(
    payload: ComputerUseStatusResponse | Mapping[str, JsonValue],
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
    else:
        # A pair this SDK release does not list. Reading it as "unknown" keeps the raw values
        # and lets a deployed client survive a vocabulary the server grew.
        return ComputerUseUnknownStatus(
            raw_status=status,
            raw_desktop=desktop,
            cause=_parse_computer_use_desktop_failure(model.cause) if model.cause is not None else None,
            extra_fields=extra_fields,
        )

    cause_state = "absent" if model.cause is None else "present"
    raise create_error(
        "Invalid computer-use status response: "
        f"received status={status!r}, desktop={desktop!r}, cause={cause_state}; "
        "expected inactive/not_started or inactive/starting without cause, "
        "active/ready without cause, or inactive/failed with a well-formed cause",
        400,
        "BAD_REQUEST",
        "SDK",
    )


def parse_computer_use_restart(
    payload: ComputerUseRestartResponse | Mapping[str, JsonValue],
) -> ComputerUseRestartResponseInfo:
    if isinstance(payload, ComputerUseRestartResponse):
        model = payload
    else:
        try:
            model = expect_computer_use_restart_response(dict(payload))
        except DaytonaError as exc:
            raise create_error(
                f"Invalid computer-use restart response: {exc.message}",
                400,
                "BAD_REQUEST",
                "SDK",
            ) from exc
    status = enum_str(model.status)
    desktop = enum_str(model.desktop)
    if status != "active" or desktop != "ready":
        raise create_error(
            "Invalid computer-use restart response: "
            f"received message={model.message!r}, status={status!r}, desktop={desktop!r}; "
            "expected a string message with status='active' and desktop='ready'",
            400,
            "BAD_REQUEST",
            "SDK",
        )
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
_MAX_EXACT_INTEGER = 2**53 - 1


def _restart_backoff_seconds(milliseconds: int) -> float:
    if not 0 <= milliseconds <= _MAX_EXACT_INTEGER:
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
        result[name] = ComputerUseProcessStatusInfo(
            running=raw.running,
            priority=raw.priority,
            auto_restart=raw.auto_restart,
            pid=raw.pid,
            failed=raw.failed,
            restart_attempts=raw.restart_attempts,
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


def parse_computer_use_point(payload: ComputerUsePoint) -> ComputerUsePointInfo:
    return ComputerUsePointInfo(x=payload.x, y=payload.y)


def parse_computer_use_screenshot(payload: ComputerUseScreenshotResponse) -> ComputerUseScreenshot:
    cursor_position = (
        parse_computer_use_point(payload.cursor_position) if payload.cursor_position is not None else None
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
    """A JSON number the decoder read as ``inf`` (``1e309``) is no number."""
    if value is not None and not math.isfinite(value):
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
    return ComputerUseDisplayInfo(
        id=payload.id,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
        is_active=payload.is_active,
    )


def _parse_computer_use_window(payload: ComputerUseWindow) -> ComputerUseWindowInfo:
    return ComputerUseWindowInfo(
        id=payload.id,
        title=payload.title,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
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
            x=bounds_payload.x,
            y=bounds_payload.y,
            width=bounds_payload.width,
            height=bounds_payload.height,
        ),
        states=payload.states,
        actions=payload.actions,
        children=children,
        value=payload.value,
        value_truncated=bool(payload.value_truncated),
    )
