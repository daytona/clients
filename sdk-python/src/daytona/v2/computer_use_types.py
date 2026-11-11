# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Literal, Optional, TypeGuard, Union

from . import _gen
from .types import JsonValue

ComputerUseMouseButton = Literal["left", "right", "middle"]
ComputerUseScrollDirection = Literal["up", "down"]
ComputerUseScreenshotFormat = Literal["png", "jpeg"]
ComputerUseKeyboardModifier = Literal["ctrl", "alt", "shift", "cmd"]
ComputerUseA11yScope = Literal["focused", "pid", "all"]
ComputerUseA11yNameMatch = Literal["exact", "substring", "regex"]
# "unknown" is this SDK release's reading of a (status, desktop) pair it does
# not recognize; the wire values are kept on ComputerUseUnknownStatus.
ComputerUseActivityStatus = Literal["active", "inactive", "unknown"]
ComputerUseDesktopState = Literal["not_started", "starting", "ready", "failed", "unknown"]
ComputerUseDesktopFailureCode = Literal[
    "desktop_exited",
    "addon_crashed",
    "start_failed",
    "start_interrupted",
    "session_ended",
    "session_replaced",
    "state_corrupt",
    "state_persistence_failed",
    "unknown",
]
_COMPUTER_USE_DESKTOP_FAILURE_CODE_VALUES = tuple(
    str(member.value)
    for member in _gen.toolbox.ComputerUseDesktopFailureCode
    if member.name != "UNKNOWN_DEFAULT_OPEN_API"
)


def _is_computer_use_desktop_failure_code_known(
    value: str,
) -> TypeGuard[
    Literal[
        "desktop_exited",
        "addon_crashed",
        "start_failed",
        "start_interrupted",
        "session_ended",
        "session_replaced",
        "state_corrupt",
        "state_persistence_failed",
    ]
]:
    return value in _COMPUTER_USE_DESKTOP_FAILURE_CODE_VALUES


def _computer_use_desktop_failure_code_or_unknown(value: str) -> ComputerUseDesktopFailureCode:
    if _is_computer_use_desktop_failure_code_known(value):
        return value
    return "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseDesktopFailure:
    code: ComputerUseDesktopFailureCode
    raw_code: str
    message: str
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseNotStartedStatus:
    status: Literal["inactive"] = "inactive"
    desktop: Literal["not_started"] = "not_started"
    cause: None = None
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseStartingStatus:
    status: Literal["inactive"] = "inactive"
    desktop: Literal["starting"] = "starting"
    cause: None = None
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseReadyStatus:
    status: Literal["active"] = "active"
    desktop: Literal["ready"] = "ready"
    cause: None = None
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseFailedStatus:
    cause: ComputerUseDesktopFailure
    status: Literal["inactive"] = "inactive"
    desktop: Literal["failed"] = "failed"
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseUnknownStatus:
    """A (status, desktop) pair this SDK release does not recognize.
    raw_status / raw_desktop carry the API values or combination;
    the recognized variants need no raw sibling, since
    their typed values are the wire values. cause is the failure the API
    attached to the unrecognized state, when it sent one."""

    raw_status: str
    raw_desktop: str
    status: Literal["unknown"] = "unknown"
    desktop: Literal["unknown"] = "unknown"
    cause: Optional[ComputerUseDesktopFailure] = None
    extra_fields: Optional[Dict[str, JsonValue]] = None


ComputerUseStatusResponse = Union[
    ComputerUseNotStartedStatus,
    ComputerUseStartingStatus,
    ComputerUseReadyStatus,
    ComputerUseFailedStatus,
    ComputerUseUnknownStatus,
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseResolution:
    """A desktop resolution in pixels: 1024x768, 1280x720 or 1920x1080."""

    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseRestartResponse:
    message: str
    status: Literal["active"] = "active"
    desktop: Literal["ready"] = "ready"
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseProcessStatus:
    running: bool
    priority: int
    auto_restart: bool
    failed: bool
    restart_attempts: int
    restart_backoff: float
    """The current restart backoff, in seconds."""
    status: str
    pid: Optional[int] = None
    extra_fields: Optional[Dict[str, JsonValue]] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseLifecycleResult:
    message: str
    status: Dict[str, ComputerUseProcessStatus]


ComputerUseLifecycleResponse = ComputerUseLifecycleResult


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseProcessRunningStatus:
    process_name: str
    running: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseProcessRestartResult:
    message: str
    process_name: str


ComputerUseProcessRestartResponse = ComputerUseProcessRestartResult


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseProcessLogs:
    process_name: str
    logs: str


ComputerUseProcessLogsResponse = ComputerUseProcessLogs


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseProcessErrors:
    process_name: str
    errors: str


ComputerUseProcessErrorsResponse = ComputerUseProcessErrors


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUsePoint:
    x: int
    y: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseRegion:
    """A screen rectangle for ``screen.screenshot(region=...)``."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseScreenshot:
    screenshot: str
    """The image, base64-encoded."""
    size_bytes: int
    cursor_position: Optional[ComputerUsePoint] = None


ComputerUseScreenshotResponse = ComputerUseScreenshot


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseScrollResult:
    success: bool


ComputerUseScrollResponse = ComputerUseScrollResult


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseDisplay:
    id: int
    x: int
    y: int
    width: int
    height: int
    is_active: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseDisplayInfo:
    displays: tuple[ComputerUseDisplay, ...]


ComputerUseDisplayInfoResponse = ComputerUseDisplayInfo


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseWindow:
    id: int
    title: str
    x: int
    y: int
    width: int
    height: int
    is_active: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseDisplayWindows:
    windows: tuple[ComputerUseWindow, ...]


ComputerUseDisplayWindowsResponse = ComputerUseDisplayWindows


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseAccessibilityBounds:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseAccessibilityNode:
    id: str
    role: str
    name: str
    bounds: ComputerUseAccessibilityBounds
    states: tuple[str, ...]
    description: Optional[str] = None
    actions: Optional[tuple[str, ...]] = None
    children: Optional[tuple[ComputerUseAccessibilityNode, ...]] = None
    # A control's current value (macOS). Never set for password fields.
    value: Optional[str] = None
    # `value` was cut at the platform's cap. False when uncut or not reported; the SDK always fills it.
    value_truncated: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseA11yTree:
    root: ComputerUseAccessibilityNode
    truncated: bool


ComputerUseA11yTreeResponse = ComputerUseA11yTree


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseA11yFindResult:
    matches: tuple[ComputerUseAccessibilityNode, ...]
    truncated: bool


ComputerUseA11yFindResponse = ComputerUseA11yFindResult


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseRecording:
    id: str
    file_name: str
    file_path: str
    start_time: datetime
    status: str
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    size_bytes: Optional[int] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ComputerUseRecordingList:
    recordings: tuple[ComputerUseRecording, ...]


ComputerUseRecordingsResponse = ComputerUseRecordingList
