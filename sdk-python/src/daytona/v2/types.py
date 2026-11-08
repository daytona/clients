# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Public DTOs for the V2 Python SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import TracebackType
from typing import (
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterator,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    TypedDict,
    TypeGuard,
    TypeVar,
    Union,
    get_args,
)

from typing_extensions import NotRequired, Self

from . import _gen
from .errors import create_error

AuditActionList = _gen.api.AuditActionList
AuditActionGroup = _gen.api.AuditActionGroup
ProjectApiKeyWithContext = _gen.api.ProjectApiKeyWithContext
ImageBuildResponse = _gen.api.AsyncOperation
ProjectBudget = _gen.api.ProjectBudget
OrganizationLimits = _gen.api.OrganizationLimits
ImageListItem = _gen.api.ImageListItem
Secret = _gen.api.Secret
SandboxStateSnapshot = _gen.api.SnapshotListItem
SnapshotDetail = _gen.api.Snapshot
MacModel = _gen.api.MacModel
MacModelList = _gen.api.MacModelList
MacResources = _gen.api.MacResources
ReservationPage = _gen.api.ReservationPage
# ``GET /mac-catalog``: per region, the default model and each model's
# ``MacModel``, free Macs (``available``) and ``price`` (``None`` when pricing is unavailable).
MacCatalog = _gen.api.MacCatalog
MacReservationStatus = Literal[
    "pending_charge", "pending_mac", "active", "ending", "released", "failed", "unknown"
]
MacReservationEndReason = Literal[
    "expired", "released", "charge_failed", "not_accepted", "reissued", "operator", "unknown"
]
# ``explicit``: bought by ``reservations.create()`` or a macOS create with
# ``reservation="new"``; ``auto``: bought by the organization's auto-reserve.
MacReservationOrigin = Literal["explicit", "auto", "unknown"]
# How a macOS sandbox resumed, as reported by the API.
MacBootMode = str

JsonScalar = Union[str, int, float, bool, None]
JsonValue = Union[JsonScalar, Sequence["JsonValue"], Mapping[str, "JsonValue"]]
JsonObject = Dict[str, JsonValue]
QueryParamScalar = Union[str, int, float, bool, None]
QueryParamValue = Union[QueryParamScalar, List[QueryParamScalar]]
T = TypeVar("T")
TransferProgressCallback = Callable[[int, Optional[int]], None]


class TransferCancelSignal(Protocol):
    """A cancellation signal for file transfers.

    ``asyncio.Event`` and ``threading.Event`` both satisfy this protocol.
    """

    def is_set(self) -> bool: ...


SandboxState = Literal[
    "queued",
    "creating",
    "starting",
    "started",
    "pausing",
    "paused",
    "stopping",
    "stopped",
    "destroying",
    "destroyed",
    "error",
    "unknown",
]
SandboxWaitState = Literal[
    "queued",
    "starting",
    "started",
    "pausing",
    "paused",
    "stopping",
    "stopped",
    "destroying",
    "destroyed",
]
# States from which no wait target can ever be reached again. ``stopped`` /
# ``paused`` are deliberately NOT here: they are resumable, so a caller waiting
# for ``started`` may legitimately expect someone to start() the sandbox. Pass
# ``terminal_states=`` explicitly to fail fast on those.
DEFAULT_SANDBOX_TERMINAL_STATES: tuple[SandboxState, ...] = ("destroying", "destroyed", "error")


def _enum_values(enum_type: type[Enum]) -> tuple[str, ...]:
    return tuple(str(member.value) for member in enum_type if member.name != "UNKNOWN_DEFAULT_OPEN_API")


ProcessKind = Literal["exec", "pty", "code", "session"]
ProcessReportedKind = Literal["exec", "pty", "code", "session", "unknown"]
ProcessStateFilter = Literal["running", "terminal", "all"]
ProcessState = Literal["starting", "provisioning", "running", "terminal", "unknown"]
LogEncoding = Literal["text", "base64"]
KeepLogs = Literal["until_cleanup", "on_exit_ttl", "none"]
# Retention for run() / session.run() / sandbox.exec(): they read the output
# back from the retained log, so "none" is not accepted.
RunKeepLogs = Literal["until_cleanup", "on_exit_ttl"]
StdinMode = Literal["closed", "open"]
ProcessKeepLogs = Literal["until_cleanup", "on_exit_ttl", "none", "unknown"]
ProcessStdinMode = Literal["closed", "open", "unknown"]
ProcessReason = Literal["exited", "signaled", "timed_out", "lost", "failed", "unknown"]
ProcessSignal = Literal["TERM", "KILL", "INT", "HUP", "QUIT", "USR1", "USR2"]
SandboxClass = Literal["linux", "macos"]
ArtifactRetention = Literal["permanent", "idle", "unknown"]
SnapshotKind = Literal["full", "unknown"]
VolumeState = Literal["ready", "deleting", "deleted", "error", "unknown"]
VolumeType = Literal["disk", "bucket", "unknown"]
SearchTotalRelation = Literal["eq", "gte", "unknown"]
DockerState = Literal[
    "ready",
    "starting",
    "failed",
    "not_present",
    "disabled",
    "unmanaged",
    "deferred",
    "unsupported",
    "unknown",
]
DockerOwner = Literal["daemon", "entrypoint", "none", "unknown"]

_PROCESS_INFO_KIND_VALUES = _enum_values(_gen.toolbox.Kind)
_PROCESS_STATE_VALUES = _enum_values(_gen.toolbox.State)
_PROCESS_KEEP_LOGS_VALUES = _enum_values(_gen.toolbox.KeepLogs)
_PROCESS_STDIN_MODE_VALUES = _enum_values(_gen.toolbox.StdinMode)
_WAIT_REASON_VALUES = _enum_values(_gen.toolbox.Reason)
_DOCKER_STATE_VALUES = _enum_values(_gen.toolbox.DockerState)
_DOCKER_OWNER_VALUES = _enum_values(_gen.toolbox.DockerOwner)


def _is_process_info_kind_known(value: str) -> TypeGuard[ProcessKind]:
    return value in _PROCESS_INFO_KIND_VALUES


def _is_process_state_known(
    value: str,
) -> TypeGuard[Literal["starting", "provisioning", "running", "terminal"]]:
    return value in _PROCESS_STATE_VALUES


def _is_process_keep_logs_known(value: str) -> TypeGuard[KeepLogs]:
    return value in _PROCESS_KEEP_LOGS_VALUES


def _is_process_stdin_mode_known(value: str) -> TypeGuard[StdinMode]:
    return value in _PROCESS_STDIN_MODE_VALUES


def _is_wait_reason_known(
    value: str,
) -> TypeGuard[Literal["exited", "signaled", "timed_out", "lost", "failed"]]:
    return value in _WAIT_REASON_VALUES


def _is_docker_state_known(
    value: str,
) -> TypeGuard[
    Literal["ready", "starting", "failed", "not_present", "disabled", "unmanaged", "deferred", "unsupported"]
]:
    return value in _DOCKER_STATE_VALUES


def _is_docker_owner_known(value: str) -> TypeGuard[Literal["daemon", "entrypoint", "none"]]:
    return value in _DOCKER_OWNER_VALUES


def _process_info_kind_or_unknown(value: str) -> ProcessReportedKind:
    if _is_process_info_kind_known(value):
        return value
    return "unknown"


def _process_state_or_unknown(value: str) -> ProcessState:
    if _is_process_state_known(value):
        return value
    return "unknown"


def _process_keep_logs_or_unknown(value: str) -> ProcessKeepLogs:
    if _is_process_keep_logs_known(value):
        return value
    return "unknown"


def _process_stdin_mode_or_unknown(value: str) -> ProcessStdinMode:
    if _is_process_stdin_mode_known(value):
        return value
    return "unknown"


def _wait_reason_or_unknown(value: str) -> ProcessReason:
    if _is_wait_reason_known(value):
        return value
    return "unknown"


def _docker_state_or_unknown(value: str) -> DockerState:
    if _is_docker_state_known(value):
        return value
    return "unknown"


def _docker_owner_or_unknown(value: str) -> DockerOwner:
    if _is_docker_owner_known(value):
        return value
    return "unknown"


FilesystemEventType = Union[Literal["create", "write", "remove", "rename", "chmod"], str]
"""What changed in a watched directory: ``"create"``, ``"write"``, ``"remove"``,
``"rename"`` or ``"chmod"`` today. An open vocabulary: a kind a later sandbox
reports is passed through as its string."""


LifecycleAction = Literal["stop", "pause", "delete"]
"""The action a lifecycle trigger fires: ``on_timeout`` / ``on_idle_timeout``.

Available actions depend on the sandbox's class and persistence; unsupported settings
are rejected before sending the request.
"""


DISABLED: int = -1
"""Clears :attr:`LifecyclePolicyConfig.idle_expiry_seconds` — and only that field.

``idle_expiry_seconds`` value space, identical at create and on
:meth:`daytona.v2.Sandbox.update_lifecycle_policy`: omitted = unchanged (update) /
disabled (create); ``DISABLED`` (-1) = clear the idle pair (``on_idle_timeout``
with it); ``n > 0`` = seconds, a value below 60 raised to 60 server-side and
read back raised; ``0`` and anything below -1 are rejected.

``timeout_seconds`` does NOT accept it: the timeout cannot be disabled, so
``timeout_seconds`` must be ``>= 1`` (``-1`` and ``0`` are rejected).
"""


class LifecyclePolicyConfig(TypedDict, total=False):
    """Per-sandbox lifecycle policy: a wall-clock timeout and an idle timeout,
    each paired with the action it fires.

    ``timeout_seconds`` (``>= 1``, never disabled) fires ``on_timeout`` that many
    seconds after create, after a :meth:`daytona.v2.Sandbox.update_lifecycle_policy` that
    supplies it, and again after every accepted transition to running (start,
    resume) — the full budget re-arms each time. Omitted ``on_timeout``
    resolves to the server's class default.

    ``idle_expiry_seconds`` is opt-in and fires ``on_idle_timeout`` after that many
    seconds without activity; :data:`DISABLED` clears it. ``on_idle_timeout`` has
    no default — arming ``idle_expiry_seconds`` from nothing requires it.

    At Linux create, each field you omit is inherited from the project's
    lifecycle defaults (set in the dashboard's project settings), then — for
    ``timeout_seconds`` only — from the platform default timeout when enabled.
    macOS creates use platform defaults, not project defaults.
    An action follows its own interval: a
    ``timeout_seconds`` you pass without ``on_timeout`` gets the class default, not
    the project's action. Pass :data:`DISABLED` as ``idle_expiry_seconds`` to opt
    out of an inherited idle timeout. Project defaults apply to new sandboxes
    only; :meth:`daytona.v2.Sandbox.update_lifecycle_policy` never consults them.

    Settings unavailable for the sandbox's class and persistence are rejected before
    sending the request. Linux supports ``delete``; persistent macOS sandboxes
    also support ``stop``. Pause macOS explicitly with :meth:`daytona.v2.Sandbox.pause`.
    """

    timeout_seconds: int
    on_timeout: LifecycleAction
    idle_expiry_seconds: int
    on_idle_timeout: LifecycleAction


@dataclass(frozen=True)
class ListResult(Generic[T]):
    """Cursor-paginated list result.

    ``total`` and ``total_relation`` are only populated
    when the result came from search (``Daytona.list()`` with any
    search filter); they are ``None`` on every other list surface.
    """

    items: List[T]
    next_cursor: Optional[str] = None
    total: Optional[int] = None
    total_relation: Optional[SearchTotalRelation] = None
    raw_total_relation: Optional[str] = None

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)


@dataclass(frozen=True)
class SearchResult(Generic[T]):
    """One page of ``GET /sandboxes/search`` (``Daytona.search()``).

    ``total`` is exact when ``total_relation == "eq"`` and a lower bound when it
    is ``"gte"`` (exact counting is capped at 10 000).
    """

    items: List[T]
    next_cursor: Optional[str] = None
    total: int = 0
    total_relation: SearchTotalRelation = "eq"
    raw_total_relation: str = "eq"

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)


@dataclass(frozen=True)
class SandboxEventsConfig:
    """Configuration for the sandbox-state events hub.

    ``enabled`` — set to ``False`` (or ``DAYTONA_SANDBOX_EVENTS=0``) to fall
    back to long-poll for every wait.  ``reconcile_interval`` is in seconds
    (``0`` / ``None`` disables the timer).  ``idle_close`` (seconds) closes
    idle sockets; ``None`` keeps them open indefinitely.
    """

    enabled: bool = True
    reconcile_interval: Optional[float] = 60.0
    idle_close: Optional[float] = None


@dataclass(frozen=True)
class DaytonaConfig:
    """Configuration for the v2 Daytona client.

    There is no API URL here: the client reads ``DAYTONA_V2_API_URL`` (a
    regional endpoint or a test environment), else uses ``DEFAULT_V2_API_URL``; ``DAYTONA_API_URL``
    belongs to the 0.x SDK and is never read. There is no region either: pass
    ``region=`` to a create.

    ``request_timeout`` is the total time one ordinary request may take, in
    seconds (default 60; ``0`` = no limit). Waits and long polls use their own
    budget, streams are bounded only until their response headers arrive, and
    file transfers are not bounded.  ``shared_hub`` controls whether this instance
    shares the shared :class:`~daytona.v2._events.WaitHub` for its event loop and
    hub identity (default) or gets its own private one.  ``events`` customises
    that identity and the hub's behaviour; ``None`` uses defaults.
    """

    api_key: Optional[str] = field(default=None, repr=False)
    jwt_token: Optional[str] = field(default=None, kw_only=True, repr=False)
    project_id: Optional[str] = field(default=None, kw_only=True)
    otel_enabled: Optional[bool] = field(default=None, kw_only=True)
    connection_pool_maxsize: Optional[int] = field(default=None, kw_only=True)
    request_timeout: Optional[float] = None
    shared_hub: bool = True
    events: Optional[SandboxEventsConfig] = None


@dataclass(frozen=True)
class Resources:
    """Sandbox resource request; CPU is cores (1 core = 2 vCPUs), memory and disk are GiB.

    ``cpu``/``memory``/``disk`` are the request: what is reserved and billed as a
    floor. ``cpu_max``/``memory_max``/``disk_max`` are the limits: the boot shape
    and the ceiling, the system cap when omitted. Set each limit equal to its
    request for a fixed shape. Requests below the platform minimums are raised
    to them, so a fixed shape below a minimum is refused. ``cpu_max`` is a
    multiple of 0.5 cores: the sandbox boots ``cpu_max * 2`` vCPUs.

    """

    cpu: Optional[float] = None
    cpu_max: Optional[float] = None
    memory: Optional[int] = None
    memory_max: Optional[float] = None
    disk: Optional[int] = None
    disk_max: Optional[float] = None


@dataclass(frozen=True)
class PortMapping:
    port: int
    proto: str = "tcp"


@dataclass(frozen=True)
class VolumeMount:
    """Attach a volume to a sandbox."""

    volume_id: str
    mount_path: str
    subpath: Optional[str] = None
    # Read-only mounts are supported for bucket volumes, not disk volumes.
    read_only: Optional[bool] = None


class NetworkPolicyInput(TypedDict, total=False):
    block_all: bool | None
    allow_cidrs: Sequence[str]
    allow_domains: Optional[Sequence[str]]


@dataclass(frozen=True)
class NetworkPolicy:
    version: int
    block_all: bool = False
    allow_cidrs: List[str] = field(default_factory=list)
    allow_domains: Optional[List[str]] = None


@dataclass(frozen=True)
class NetworkPolicyInfo:
    desired: Optional[NetworkPolicy]
    desired_revision: int
    etag: str
    http_status: int
    applied: Optional[NetworkPolicy] = None
    applied_revision: Optional[int] = None
    applied_start_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class SandboxSecretsInfo:
    secrets: List[str]
    desired_revision: int
    etag: str
    http_status: int
    applied_revision: Optional[int] = None
    status: Optional[str] = None
    error: Optional[str] = None
    capture_status: Optional[str] = None
    capture_unresolved_count: Optional[int] = None


@dataclass(frozen=True)
class SandboxInfo:
    """Serializable sandbox DTO."""

    id: str
    name: Optional[str] = None
    state: SandboxState = "unknown"
    image: Optional[ImageRef] = None
    target: Optional[str] = None
    #: The memory snapshot this sandbox resumed, or ``None`` when it booted an image.
    snapshot: Optional[SnapshotRef] = None
    #: The image build this sandbox booted from, or ``None`` for other image sources.
    image_build_id: Optional[str] = None
    # Optional metadata fields are keyword-only (per-field, not the KW_ONLY
    # sentinel: Python 3.10's
    # typing.get_type_hints() cannot evaluate the sentinel's annotation).
    region: Optional[str] = field(default=None, kw_only=True)
    labels: Dict[str, str] = field(default_factory=dict, kw_only=True)
    #: Reserved CPU (the guarantee), in cores (1 core = 2 vCPUs; a macOS core is one Mac CPU).
    cpu: Optional[float] = field(default=None, kw_only=True)
    #: CPU limit (the boot shape), in cores; ``None`` when the API did not report it.
    cpu_max: Optional[float] = field(default=None, kw_only=True)
    #: GiB. macOS reports its assigned size exactly (4608 MiB -> 4.5 GiB,
    #: 117760 MiB -> 115 GiB; 0 before assignment); Linux reports whole GiB.
    memory: Optional[float] = field(default=None, kw_only=True)
    memory_max: Optional[float] = field(default=None, kw_only=True)
    disk: Optional[float] = field(default=None, kw_only=True)
    disk_max: Optional[float] = field(default=None, kw_only=True)
    sandbox_class: Optional[SandboxClass] = field(default=None, kw_only=True)
    persistent: Optional[bool] = field(default=None, kw_only=True)
    spot: Optional[bool] = field(default=None, kw_only=True)
    spot_evicted_at: Optional[str] = field(default=None, kw_only=True)
    lifecycle_policy: Optional[SandboxLifecycle] = field(default=None, kw_only=True)
    forked_from: Optional[str] = field(default=None, kw_only=True)
    #: macOS only: the external ID of the reservation selected by the current or
    #: latest successful claim. Unset before first claim, and on search
    #: reads (``search()``, a search-routed ``list()``), which omit it:
    #: ``refresh_data()`` reads it.
    reservation_id: Optional[str] = field(default=None, kw_only=True)
    user: Optional[str] = field(default=None, kw_only=True)
    error: Optional[str] = field(default=None, kw_only=True)

    def to_json(self) -> Dict[str, JsonValue]:
        """Return a JSON-safe sandbox-info payload."""
        payload: Dict[str, JsonValue] = {
            "id": self.id,
            "state": self.state,
            "labels": dict(self.labels),
        }
        _put_json_value(payload, "name", self.name)
        _put_json_value(payload, "image", _artifact_ref_to_json(self.image))
        _put_json_value(payload, "snapshot", _artifact_ref_to_json(self.snapshot))
        _put_json_value(payload, "target", self.target)
        _put_json_value(payload, "region", self.region)
        _put_json_value(payload, "image_build_id", self.image_build_id)
        _put_json_value(payload, "cpu", self.cpu)
        _put_json_value(payload, "cpu_max", self.cpu_max)
        _put_json_value(payload, "memory", self.memory)
        _put_json_value(payload, "memory_max", self.memory_max)
        _put_json_value(payload, "disk", self.disk)
        _put_json_value(payload, "disk_max", self.disk_max)
        _put_json_value(payload, "sandbox_class", self.sandbox_class)
        _put_json_value(payload, "persistent", self.persistent)
        _put_json_value(payload, "spot", self.spot)
        _put_json_value(payload, "spot_evicted_at", self.spot_evicted_at)
        _put_json_value(payload, "forked_from", self.forked_from)
        _put_json_value(payload, "reservation_id", self.reservation_id)
        _put_json_value(payload, "user", self.user)
        _put_json_value(payload, "error", self.error)
        if self.lifecycle_policy is not None:
            payload["lifecycle_policy"] = _sandbox_lifecycle_to_json(self.lifecycle_policy)
        return payload

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> Self:
        """Rehydrate sandbox info from :meth:`to_json` output."""
        target = _json_str(value.get("target"))
        region = _json_str(value.get("region"))
        lifecycle_value = value.get("lifecycle_policy")
        return cls(
            id=_json_required_str(value.get("id"), "id"),
            name=_json_str(value.get("name")),
            state=_json_sandbox_state(value.get("state")),
            image=_json_image_ref(value.get("image")),
            snapshot=_json_snapshot_ref(value.get("snapshot")),
            target=target,
            region=region,
            labels=_json_string_map(value.get("labels")),
            image_build_id=_json_str(value.get("image_build_id")),
            cpu=_json_number(value.get("cpu")),
            cpu_max=_json_number(value.get("cpu_max")),
            memory=_json_gib(value.get("memory")),
            memory_max=_json_gib(value.get("memory_max")),
            disk=_json_gib(value.get("disk")),
            disk_max=_json_gib(value.get("disk_max")),
            sandbox_class=_json_sandbox_class(value.get("sandbox_class")),
            persistent=_json_bool(value.get("persistent")),
            spot=_json_bool(value.get("spot")),
            spot_evicted_at=_json_str(value.get("spot_evicted_at")),
            lifecycle_policy=_json_sandbox_lifecycle(lifecycle_value),
            forked_from=_json_str(value.get("forked_from")),
            reservation_id=_json_str(value.get("reservation_id")),
            user=_json_str(value.get("user")),
            error=_json_str(value.get("error")),
        )


def _artifact_ref_to_json(ref: ImageRef | SnapshotRef | None) -> JsonValue:
    """An image or snapshot reference containing its ID and optional name."""
    if ref is None:
        return None
    return {"id": ref.id, "name": ref.name}


def _json_artifact_ref_parts(value: JsonValue) -> Optional[tuple[str, Optional[str]]]:
    if isinstance(value, Mapping):
        ref_id = value.get("id")
        if isinstance(ref_id, str) and ref_id:
            name = value.get("name")
            return ref_id, name if isinstance(name, str) else None
    return None


def _json_image_ref(value: JsonValue) -> Optional[ImageRef]:
    parts = _json_artifact_ref_parts(value)
    return ImageRef(id=parts[0], name=parts[1]) if parts else None


def _json_snapshot_ref(value: JsonValue) -> Optional[SnapshotRef]:
    parts = _json_artifact_ref_parts(value)
    return SnapshotRef(id=parts[0], name=parts[1]) if parts else None


def _put_json_value(payload: Dict[str, JsonValue], key: str, value: JsonValue) -> None:
    if value is not None:
        payload[key] = value


def _json_required_str(value: JsonValue, field_name: str) -> str:
    if isinstance(value, str):
        return value
    raise create_error(f"SandboxInfo.{field_name} must be a string", 400, "BAD_REQUEST", "SDK")


def _json_str(value: JsonValue) -> Optional[str]:
    return value if isinstance(value, str) else None


def _json_bool(value: JsonValue) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _json_int(value: JsonValue) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _json_number(value: JsonValue) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _json_gib(value: JsonValue) -> Optional[float]:
    """A GiB amount: whole for Linux, possibly fractional (4.5) for macOS."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _json_string_map(value: JsonValue) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}


def _json_sandbox_class(value: JsonValue) -> Optional[SandboxClass]:
    if value in ("linux", "macos"):
        return value
    return None


def _sandbox_lifecycle_to_json(value: SandboxLifecycle) -> JsonObject:
    payload: JsonObject = {}
    _put_json_value(payload, "timeout_seconds", value.timeout_seconds)
    _put_json_value(payload, "on_timeout", value.on_timeout)
    _put_json_value(payload, "timeout_at", value.timeout_at)
    _put_json_value(payload, "idle_expiry_seconds", value.idle_expiry_seconds)
    _put_json_value(payload, "on_idle_timeout", value.on_idle_timeout)
    _put_json_value(payload, "idle_timeout_at", value.idle_timeout_at)
    _put_json_value(payload, "last_activity_at", value.last_activity_at)
    _put_json_value(payload, "last_activity_source", value.last_activity_source)
    return payload


def _json_sandbox_lifecycle(value: JsonValue) -> Optional[SandboxLifecycle]:
    if not isinstance(value, Mapping):
        return None
    return SandboxLifecycle(
        timeout_seconds=_json_int(value.get("timeout_seconds")),
        on_timeout=_json_str(value.get("on_timeout")),
        timeout_at=_json_str(value.get("timeout_at")),
        idle_expiry_seconds=_json_int(value.get("idle_expiry_seconds")),
        on_idle_timeout=_json_str(value.get("on_idle_timeout")),
        idle_timeout_at=_json_str(value.get("idle_timeout_at")),
        last_activity_at=_json_str(value.get("last_activity_at")),
        last_activity_source=_json_str(value.get("last_activity_source")),
    )


def _is_sandbox_state(value: str) -> TypeGuard[SandboxState]:
    return value in get_args(SandboxState)


def _json_sandbox_state(value: JsonValue) -> SandboxState:
    if isinstance(value, str) and _is_sandbox_state(value):
        return value
    return "unknown"


@dataclass(frozen=True, slots=True)
class ImageRef:
    id: str
    name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    id: str
    name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Volume:
    id: str
    name: str
    type: VolumeType
    raw_type: str
    status: VolumeState
    raw_status: str
    created_at: datetime
    updated_at: datetime
    size_gib: Optional[int] = None
    error: Optional[str] = None
    last_used_at: Optional[datetime] = None
    options: Optional[JsonObject] = None
    used_bytes: Optional[int] = None
    used_bytes_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class MacReservation:
    id: str
    org_id: str
    project_id: str
    region: str
    status: MacReservationStatus
    raw_status: str
    sandboxes_per_mac: int
    period_n: int
    period_seconds: int
    created_at: datetime
    slots_used: int
    renewal_pending: bool
    auto_renew: bool
    origin: MacReservationOrigin
    raw_origin: str
    resources_used: MacResources
    activated_at: Optional[datetime] = None
    capacity: Optional[MacResources] = None
    model: Optional[str] = None
    end_reason: Optional[MacReservationEndReason] = None
    raw_end_reason: Optional[str] = None
    ended_at: Optional[datetime] = None
    # When the reservation last began waiting for a Mac: at its first charge,
    # or when it moved after its Mac was lost. Kept after a Mac claims it.
    pending_mac_started_at: Optional[datetime] = None
    period_end: Optional[datetime] = None
    period_started_at: Optional[datetime] = None
    reissued_at: Optional[datetime] = None
    renewal_error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class MacReservationBatch:
    """``POST /reservations/bulk``: ``requested`` (the request's count), ``reserved``, and one reservation per reserved Mac."""

    requested: int
    reserved: int
    reservations: List[MacReservation]


class RegistryImageSource(TypedDict):
    type: Literal["registry"]
    ref: str


class DockerfileImageSource(TypedDict):
    type: Literal["dockerfile"]
    context_object: str
    dockerfile: str


class NamedImageSource(TypedDict):
    type: Literal["named"]
    name: str


class ManifestImageSource(TypedDict):
    type: Literal["manifest"]
    name: str


ImageSource = Union[
    RegistryImageSource,
    DockerfileImageSource,
    NamedImageSource,
    ManifestImageSource,
]


@dataclass(frozen=True, slots=True)
class Image:
    id: str
    name: Optional[str]
    # pending | building | ready | failed: the pipeline state of the artifact
    # at its current `format_version`.
    status: str
    region: str
    anonymous: bool
    retention: ArtifactRetention
    var_class: str
    available_in: list[str]
    origin: str
    created_at: Optional[str] = None
    error: Optional[str] = None
    expires_at: Optional[str] = None
    source: Optional[ImageSource] = None
    bytes: Optional[int] = None
    layer_count: Optional[int] = None
    cursor: Optional[str] = None
    # The image format: 1 for images imported from Daytona v0, 2 for the
    # current format. None when omitted by the API. Appended after `cursor`
    # so positional construction keeps its meaning.
    format_version: Optional[int] = None
    # `format_version` is below the current one: the image converts on its
    # first Linux sandbox create, or ahead of time with `POST /images/{id}/convert`;
    # other operations refuse it with `IMAGE_FORMAT_OUTDATED` until then.
    format_outdated: bool = False
    # A platform image: published by Daytona and visible to every project.
    platform: bool = False
    # The platform default for its class: the image an imageless create boots.
    default: bool = False
    # A deprecated platform image: still boots and resolves by id, but no
    # longer listed by `list(platform="true")` or chosen as the default.
    deprecated: bool = False
    # When the image was last updated.
    # Appended last so positional construction keeps its meaning.
    updated_at: Optional[str] = None
    # The disk size in MiB that a sandbox created from this image gets when
    # the create omits `disk_mib`. Set for macOS snapshots; None for every
    # other image or when unavailable.
    disk_mib: Optional[int] = None
    raw_retention: str = field(default="", kw_only=True)


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: str
    name: str
    status: str
    region: str
    kind: SnapshotKind
    var_class: str
    available_in: list[str]
    created_at: Optional[str] = None
    error: Optional[str] = None
    bytes: Optional[int] = None
    memory_bytes: Optional[int] = None
    sandbox_id: Optional[str] = None
    raw_kind: str = field(default="", kw_only=True)
    cursor: Optional[str] = None
    # When the snapshot was last updated.
    # Appended last so positional construction keeps its meaning.
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class SandboxLifecycle:
    """Lifecycle policy as the API reports it.

    ``timeout_seconds`` is the timeout duration; ``timeout_at`` is the current absolute deadline it
    derives. ``idle_timeout_at`` is when ``on_idle_timeout`` becomes eligible
    to fire — ``last_activity_at + idle_expiry_seconds`` — and any activity
    (traffic, SSH, a non-PTY process or code execution started through the
    process API that is still running — ``exec``, ``process.start``,
    ``run_code`` and interpreter executions included — or
    :meth:`AsyncSandbox.reset_idle_timer`) pushes it; ``None``
    when the idle pair is not armed or the sandbox has never been active.
    ``timeout_at``, ``idle_timeout_at``, ``last_activity_at`` and
    ``last_activity_source`` are server-derived and read-only. An action the
    SDK does not know yet (a newer server) is passed through as its wire
    string.
    """

    timeout_seconds: Optional[int] = None
    on_timeout: Optional[str] = None
    timeout_at: Optional[str] = None
    idle_expiry_seconds: Optional[int] = None
    on_idle_timeout: Optional[str] = None
    idle_timeout_at: Optional[str] = None
    last_activity_at: Optional[str] = None
    last_activity_source: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class TerminalSize:
    """PTY terminal dimensions, in character cells."""

    cols: int
    rows: int


@dataclass(frozen=True, kw_only=True)
class ProcessInfo:
    """Process metadata, as ``process.get()`` / ``process.list()`` report it.

    Enum-backed fields are normalized to the values this release knows plus
    ``"unknown"``; the wire value is kept on the ``raw_*`` sibling.
    ``signal`` is a bare name (``"TERM"``), like ``WaitResult.signal``.
    """

    id: str
    kind: ProcessReportedKind
    raw_kind: str
    state: ProcessState
    raw_state: str
    cwd: str
    stdin: ProcessStdinMode
    raw_stdin: str
    keep_logs: ProcessKeepLogs
    raw_keep_logs: str
    created_at: datetime
    system: bool = False
    name: Optional[str] = None
    pid: Optional[int] = None
    session_id: Optional[str] = None
    shell_command: Optional[str] = None
    argv: Optional[List[str]] = None
    code: Optional[str] = None
    language: Optional[str] = None
    shell: Optional[str] = None
    resolved_shell: Optional[str] = None
    login: Optional[bool] = None
    user: Optional[str] = None
    execution_timeout: Optional[float] = None
    """Server-side deadline in seconds."""
    terminal: Optional[TerminalSize] = None
    exit_code: Optional[int] = None
    signal: Optional[str] = None
    reason: Optional[ProcessReason] = None
    raw_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    exited_at: Optional[datetime] = None
    truncated_head: bool = False
    first_available_cursor: Optional[str] = None
    last_cursor: Optional[str] = None


class ProcessHandleJSON(TypedDict):
    """A process handle's serialized identity (``handle.to_json()``).

    Reconnect with ``sandbox.process.connect(value)``; the same camelCase
    shape the TypeScript SDK produces and reads.
    """

    sandboxId: str
    processId: str
    kind: NotRequired[ProcessKind]


class SessionJSON(TypedDict):
    """A shell session's serialized identity (``session.to_json()``).

    Reconnect with ``sandbox.process.connect_session(value)``.
    """

    sandboxId: str
    sessionId: str


@dataclass(frozen=True, kw_only=True)
class WaitResult:
    """How a process ended, or ``reason="timed_out"`` when the wait expired first.

    ``exit_code`` and ``signal`` are set only when the sandbox reported them;
    the SDK never fills them in. ``reason`` is normalized (``"unknown"`` for a
    value this release does not know, kept on ``raw_reason``).
    """

    reason: ProcessReason
    exit_code: Optional[int] = None
    signal: Optional[str] = None
    raw_reason: Optional[str] = None


@dataclass(frozen=True)
class DockerStatus:
    owner: DockerOwner
    state: DockerState
    process_id: Optional[str] = None
    reason: Optional[str] = None
    raw_owner: Optional[str] = None
    raw_state: Optional[str] = None


class InterpreterContextJSON(TypedDict):
    """An interpreter context's serialized identity (``context.to_json()``).

    The same camelCase shape the TypeScript SDK produces and reads.
    """

    sandboxId: str
    contextId: str
    language: str


@dataclass(frozen=True, kw_only=True)
class LogFrame:
    """One replayable process log frame."""

    cursor: str
    channel: str
    data: str
    timestamp_ms: int
    """When the frame was written, in Unix milliseconds."""


@dataclass(frozen=True, kw_only=True)
class StreamLogFrameEvent:
    """``stream_logs()``: one log frame."""

    frame: LogFrame
    cursor: str
    type: Literal["log"] = "log"


@dataclass(frozen=True, kw_only=True)
class StreamLogStateEvent:
    """``stream_logs()``: the process became terminal."""

    result: WaitResult
    cursor: str
    type: Literal["state"] = "state"


@dataclass(frozen=True, kw_only=True)
class StreamLogWarningEvent:
    """``stream_logs()``: frames before ``first_available_cursor`` were evicted."""

    message: str
    cursor: str
    first_available_cursor: str
    type: Literal["warning"] = "warning"


@dataclass(frozen=True, kw_only=True)
class StreamLogEofEvent:
    """``stream_logs()``: the log is complete; the stream ends after this event."""

    cursor: str
    type: Literal["eof"] = "eof"


StreamLogEvent = Union[StreamLogFrameEvent, StreamLogStateEvent, StreamLogWarningEvent, StreamLogEofEvent]


@dataclass(frozen=True, slots=True, kw_only=True)
class FilesystemEvent:
    type: FilesystemEventType
    name: str
    is_dir: bool
    timestamp_ms: int
    """When the change happened, in Unix milliseconds."""


@dataclass(frozen=True, kw_only=True)
class LogPage:
    """A cursor-addressed process log page."""

    items: List[LogFrame]
    next_cursor: str
    """The cursor to pass to the next ``logs()`` call to continue after this page."""
    truncated_head: bool
    """Whether retention removed frames older than this page."""
    eof: bool
    """Whether the process has ended and no frame follows this page."""
    first_available_cursor: Optional[str] = None
    """The cursor of the oldest frame still kept, when older ones were removed."""


@dataclass(frozen=True, kw_only=True)
class BulkWriteFile:
    path: str
    bytes: int


@dataclass(frozen=True, kw_only=True)
class BulkWriteResult:
    files: List[BulkWriteFile]
    """The files written."""
    errors: List[str]
    """One message per file that was not written."""


@dataclass(frozen=True, kw_only=True)
class BulkReadError:
    path: str
    message: str
    code: Optional[str] = None
    source: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class BulkReadResult:
    files: Dict[str, bytes]
    errors: List[BulkReadError] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class FileInfo:
    """One filesystem entry (``fs.list()`` / ``fs.stat()``)."""

    name: str
    path: str
    size: int
    is_dir: bool
    mode: str
    """Symbolic file mode, e.g. ``-rw-r--r--``."""
    permissions: str
    """Octal permission bits, e.g. ``0644``."""
    owner: str
    group: str
    modified_at: datetime
    mod_time: str
    """The modification time as the sandbox formats it; prefer ``modified_at``."""


@dataclass(frozen=True, kw_only=True)
class FileMatch:
    """One matching line from ``fs.grep()``."""

    file: str
    line: int
    """One-based line number."""
    content: str


@dataclass(frozen=True, kw_only=True)
class ReplaceResult:
    """One file's outcome from ``fs.replace()``."""

    file: str
    success: bool
    error: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class GitFileStatus:
    """One entry of ``GitStatus.file_status``."""

    name: str
    staging: str
    worktree: str
    extra: str
    """For a rename, the original path; otherwise empty."""


@dataclass(frozen=True, kw_only=True)
class GitStatus:
    current_branch: str
    ahead: int
    behind: int
    branch_published: bool
    file_status: List[GitFileStatus]


@dataclass(frozen=True, kw_only=True)
class GitCommitResponse:
    hash: str


@dataclass(frozen=True, kw_only=True)
class GitCommit:
    """One commit from ``git.history()``."""

    hash: str
    message: str
    author: str
    email: str
    timestamp: datetime


@dataclass(frozen=True, kw_only=True)
class GitRemote:
    name: str
    url: str


@dataclass(frozen=True, kw_only=True)
class SystemMetrics:
    """One snapshot of the sandbox's resource usage (``sandbox.metrics()``)."""

    timestamp: datetime
    cpu_count: int
    cpu_utilization: float
    """Fraction of total CPU capacity in use, between 0 and 1."""
    memory_usage_bytes: int
    memory_total_bytes: int
    disk_usage_bytes: int
    disk_total_bytes: int


@dataclass(frozen=True, kw_only=True)
class ProcessResult(WaitResult):
    """``process.run()`` result: how the process ended plus its collected output."""

    process_id: str
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    logs: List[LogFrame] = field(default_factory=list)
    """The log frames the output was collected from."""


@dataclass(frozen=True, kw_only=True)
class InterpreterProcess(WaitResult):
    process_id: Optional[str] = None
    """The code execution's process; ``None`` when the run timed out while
    queued behind earlier runs of its context and was never submitted."""


@dataclass(frozen=True, kw_only=True)
class InterpreterLogs:
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, kw_only=True)
class InterpreterError:
    name: str
    value: str
    traceback: List[str] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class InterpreterOutput:
    data: JsonObject
    is_main_result: bool
    text: Optional[str] = None
    html: Optional[str] = None
    png: Optional[str] = None
    svg: Optional[str] = None
    json: Optional[JsonValue] = None


@dataclass(frozen=True, kw_only=True)
class InterpreterResult:
    """Interpreter run result."""

    process: InterpreterProcess
    results: List[InterpreterOutput] = field(default_factory=list)
    logs: InterpreterLogs = field(default_factory=InterpreterLogs)
    error: Optional[InterpreterError] = None


@dataclass(frozen=True, slots=True)
class _InterpreterContextBinding:
    runner: Callable[[str, Optional[str], Optional[float]], Awaitable[InterpreterResult]]
    closer: Callable[[], Awaitable[None]]


class AsyncInterpreterContext:
    """An interpreter context: kernel state of its own in one sandbox.

    Built by the interpreter service (``create_context()``,
    ``connect_context()``, ``list_contexts()``), never directly. Serialize it
    with :meth:`to_json` and reconnect with ``interpreter.connect_context()``.
    """

    def __init__(self, _sandbox_id: str, _context_id: str, _language: str) -> None:
        self.sandbox_id: str = _sandbox_id
        self.context_id: str = _context_id
        self.language: str = _language
        self._binding: Optional[_InterpreterContextBinding] = None

    def __repr__(self) -> str:
        return (
            f"AsyncInterpreterContext(sandbox_id={self.sandbox_id!r}, "
            f"context_id={self.context_id!r}, language={self.language!r})"
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AsyncInterpreterContext) and (
            other.sandbox_id,
            other.context_id,
            other.language,
        ) == (self.sandbox_id, self.context_id, self.language)

    def __hash__(self) -> int:
        return hash((self.sandbox_id, self.context_id, self.language))

    def _bind(
        self,
        runner: Callable[[str, Optional[str], Optional[float]], Awaitable[InterpreterResult]],
        closer: Callable[[], Awaitable[None]],
    ) -> Self:
        self._binding = _InterpreterContextBinding(runner=runner, closer=closer)
        return self

    def _require_binding(self) -> _InterpreterContextBinding:
        if self._binding is None:
            raise create_error(
                "interpreter context is not bound to a sandbox transport", 400, "BAD_REQUEST", "SDK"
            )
        return self._binding

    def to_json(self) -> InterpreterContextJSON:
        """Return the context's JSON-safe identity for ``interpreter.connect_context()``."""
        return {"sandboxId": self.sandbox_id, "contextId": self.context_id, "language": self.language}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def run(
        self,
        code: str,
        *,
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> InterpreterResult:
        """Execute code inside this interpreter context.

        Environment variables are set when the context is created. If the
        context vanished, the API's typed ``SESSION_NOT_FOUND`` error surfaces
        unchanged so the caller can recreate the context and decide what code,
        if any, is safe to rerun. ``timeout`` is the caller wait budget in
        seconds; ``0`` waits unbounded.
        """
        return await self._require_binding().runner(code, cwd, timeout)

    async def close(self) -> None:
        """Delete this context and its kernel state."""
        await self._require_binding().closer()


@dataclass(frozen=True)
class PreviewEndpoint:
    """A sandbox HTTP preview endpoint."""

    port: int
    public: bool
    url: str
    created_at: Optional[str] = None


@dataclass(frozen=True)
class PreviewLink:
    """Preview URL plus an access token for one sandbox port.

    Send ``token`` in the ``X-Daytona-Preview-Token`` header to reach a private
    endpoint; without a credential it answers 401. Public endpoints set
    ``token`` to ``""`` and need no credential.
    ``expires_at`` (RFC 3339) is set when the token's expiry is known, else
    ``None``.

    Example — curl a private endpoint::

        link = await sandbox.ports.preview_link(8080)
        # curl -H "X-Daytona-Preview-Token: <link.token>" <link.url>

    For a shareable URL that works in a browser without extra headers, use
    :meth:`~daytona.v2.ports.AsyncPortsService.signed_preview_link` instead.
    """

    url: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class SignedPreviewLink:
    """Self-contained signed URL for one sandbox port.

    The token is embedded in the hostname — no extra headers or credentials are
    needed. The link expires at ``expires_at`` (RFC 3339). Revoke it early with
    :meth:`~daytona.v2.ports.AsyncPortsService.expire_signed_preview_link`.
    """

    url: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class SandboxAccessToken:
    """A short-lived per-sandbox access token (``POST …/access-token``).

    ``access_token`` is a bearer for sandbox services (toolbox, SSH,
    desktop, private previews) and for the self-service sandbox routes named
    by ``operations`` (``sandbox:stop`` / ``sandbox:pause`` /
    ``sandbox:delete``). It expires at ``expires_at`` (RFC 3339) and cannot
    be refreshed — mint a fresh one with
    :meth:`~daytona.v2.sandbox.AsyncSandbox.create_access_token` instead. ``ports``
    is the granted private-preview port set; ``None`` means every port.
    """

    access_token: str = field(repr=False)
    token_type: str
    expires_at: str
    operations: Sequence[str]
    ports: Optional[Sequence[int]] = None


SearchSort = Literal[
    "created_at", "updated_at", "last_activity_at", "name", "cores", "memory_mib", "disk_mib"
]
SearchOrder = Literal["asc", "desc"]
SearchTimestamp = Union[str, datetime]
"""An RFC 3339 string, or a ``datetime`` (naive values are taken as UTC)."""


@dataclass(frozen=True, kw_only=True)
class SandboxSearchFilters:
    """The ``GET /sandboxes/search`` filters shared by ``Daytona.search()`` and ``Daytona.list()``.

    ``status``, ``sandbox_class`` and ``region`` take a single value or a list;
    lists are joined with commas on the wire. ``labels`` is sent as a JSON
    object string (containment match); timestamp starts are inclusive and ends
    are exclusive.

    Every filter is keyword-only; :class:`ListSandboxesQuery` accepts its
    pagination parameters positionally.
    """

    q: Optional[str] = None
    status: Optional[Sequence[str]] = None
    sandbox_class: Optional[Union[SandboxClass, Sequence[SandboxClass]]] = None
    region: Optional[Sequence[str]] = None
    image_ref: Optional[str] = None
    image_ref_prefix: Optional[str] = None
    labels: Optional[Mapping[str, str]] = None
    spot: Optional[bool] = None
    forked_from: Optional[str] = None
    #: Bounds on ``cores_max`` (the CPU limit), in cores (1 core = 2 vCPUs; a
    #: macOS core is one Mac CPU).
    min_cores: Optional[float] = None
    max_cores: Optional[float] = None
    min_memory_mib: Optional[int] = None
    max_memory_mib: Optional[int] = None
    min_disk_mib: Optional[int] = None
    max_disk_mib: Optional[int] = None
    created_start_at: Optional[SearchTimestamp] = None
    created_end_at: Optional[SearchTimestamp] = None
    updated_start_at: Optional[SearchTimestamp] = None
    updated_end_at: Optional[SearchTimestamp] = None
    last_activity_start_at: Optional[SearchTimestamp] = None
    last_activity_end_at: Optional[SearchTimestamp] = None
    sort: Optional[SearchSort] = None
    order: Optional[SearchOrder] = None


@dataclass(frozen=True, kw_only=True)
class SearchSandboxesQuery(SandboxSearchFilters):
    """Every ``Daytona.search()`` parameter (``GET /sandboxes/search``); keyword-only."""

    name_prefix: Optional[str] = None
    id_prefix: Optional[str] = None
    cursor: Optional[str] = None
    limit: Optional[int] = None


@dataclass(frozen=True)
class ListSandboxesQuery(SandboxSearchFilters):
    """Every ``Daytona.list()`` parameter.

    ``cursor``/``limit``/``id``/``name`` remain the only positional parameters,
    in that order. ``cursor``/``limit`` alone read ``GET /sandboxes``; ``id``/``name``
    and any inherited (keyword-only) search filter route the call to
    ``/sandboxes/search``.
    """

    cursor: Optional[str] = None
    limit: Optional[int] = None
    id: Optional[str] = None
    name: Optional[str] = None


class CreateReservationRequest(TypedDict):
    """Wire body of ``POST /reservations`` (API schema ``CreateReservationRequest``).

    ``region_id`` is required.
    """

    region_id: str
    model: NotRequired[Optional[str]]
    auto_renew: NotRequired[Optional[bool]]


class CreateReservationsBulkRequest(TypedDict):
    """Wire body of ``POST /reservations/bulk`` (API schema ``CreateReservationsBulkRequest``)."""

    region_id: str
    count: int
    min_count: NotRequired[Optional[int]]
    model: NotRequired[Optional[str]]
    auto_renew: NotRequired[Optional[bool]]


class UpdateReservationRequest(TypedDict):
    """Wire body of ``PATCH /reservations/{id}`` (API schema ``UpdateReservationRequest``)."""

    auto_renew: bool
