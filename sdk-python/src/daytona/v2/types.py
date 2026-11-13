# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Public DTOs for the V2 Python SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import TracebackType
from typing import (
    Awaitable,
    Callable,
    Dict,
    Generic,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypedDict,
    TypeGuard,
    TypeVar,
    Union,
)

from typing_extensions import NotRequired, Required, Self

from . import _gen
from .errors import create_error

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


#: A sandbox CPU architecture.
CpuArch = Literal["amd64", "arm64"]

#: An image's build status; the API may add values, so any string can appear.
ImageStatus = Union[Literal["pending", "building", "ready", "failed"], str]

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
#: The sandbox states ``search(states=...)`` filters on: every
#: :data:`SandboxState` except ``"unknown"``, which names no API status.
SearchSandboxState = Literal[
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
SnapshotClass = Literal["linux", "unknown"]
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


GitConfigScope = Literal["global", "local"]
"""Which git configuration ``get_config`` / ``set_config`` read or write:
``"global"`` (the default) or ``"local"`` (a repository's, which needs
``path``)."""

LifecycleAction = Literal["stop", "pause", "delete"]
"""The action a lifecycle trigger fires: ``on_timeout`` / ``on_idle_timeout``.

Available actions depend on the sandbox's class and persistence; unsupported settings
are rejected before sending the request.
"""


DISABLED: int = -1
"""Clears :attr:`LifecyclePolicyOptions.idle_expiry_seconds` — and only that field.

``idle_expiry_seconds`` value space, identical at create and on
:meth:`daytona.v2.Sandbox.update_lifecycle_policy`: omitted = unchanged (update) /
disabled (create); ``DISABLED`` (-1) = clear the idle pair (``on_idle_timeout``
with it); ``n > 0`` = seconds, a value below 60 raised to 60 server-side and
read back raised; ``0`` and anything below -1 are rejected.

``timeout_seconds`` does NOT accept it: the timeout cannot be disabled, so
``timeout_seconds`` must be ``>= 1`` (``-1`` and ``0`` are rejected).
"""


class LifecyclePolicyOptions(TypedDict, total=False):
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


@dataclass(frozen=True, init=False)
class ListResult(Generic[T]):
    """One page of a cursor-paginated list: ``items`` and the ``next_cursor``
    to pass for the next page (``None`` on the last one). Immutable: ``items``
    is a tuple (any iterable passed in is copied into one). Keyword-only, and
    not iterable: ``.items`` is the one way to the entries, as in TypeScript.
    """

    items: Tuple[T, ...]
    next_cursor: Optional[str] = None

    def __init__(self, *, items: Iterable[T], next_cursor: Optional[str] = None) -> None:
        object.__setattr__(self, "items", tuple(items))
        object.__setattr__(self, "next_cursor", next_cursor)


@dataclass(frozen=True, init=False)
class FileSearchResult(ListResult[T]):
    """The result of ``sandbox.fs.glob()`` / ``sandbox.fs.grep()``: a
    :class:`ListResult` (one page, ``next_cursor`` always ``None``) plus
    ``skipped``, the absolute paths below the searched root that the sandbox
    user cannot read, so they were not searched (at most 1000; empty when
    nothing was skipped). Immutable: ``skipped`` is a tuple.
    """

    skipped: Tuple[str, ...] = ()

    def __init__(
        self, *, items: Iterable[T], next_cursor: Optional[str] = None, skipped: Iterable[str] = ()
    ) -> None:
        super().__init__(items=items, next_cursor=next_cursor)
        object.__setattr__(self, "skipped", tuple(skipped))


@dataclass(frozen=True, init=False)
class SearchResult(Generic[T]):
    """One page of ``GET /sandboxes/search`` (``Daytona.search()``).

    ``total`` is exact when ``total_relation == "eq"`` and a lower bound when it
    is ``"gte"`` (exact counting is capped at 10 000). Immutable, like
    :class:`ListResult`.
    """

    items: Tuple[T, ...]
    next_cursor: Optional[str] = None
    total: int = 0
    total_relation: SearchTotalRelation = "eq"
    raw_total_relation: str = "eq"

    def __init__(
        self,
        *,
        items: Iterable[T],
        next_cursor: Optional[str] = None,
        total: int = 0,
        total_relation: SearchTotalRelation = "eq",
        raw_total_relation: str = "eq",
    ) -> None:
        object.__setattr__(self, "items", tuple(items))
        object.__setattr__(self, "next_cursor", next_cursor)
        object.__setattr__(self, "total", total)
        object.__setattr__(self, "total_relation", total_relation)
        object.__setattr__(self, "raw_total_relation", raw_total_relation)


@dataclass(frozen=True, kw_only=True)
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


@dataclass(frozen=True, kw_only=True)
class DaytonaConfig:
    """Configuration for the v2 Daytona client.

    There is no API URL here: the client reads ``DAYTONA_V2_API_URL`` (a
    regional endpoint or a test environment), else uses ``DEFAULT_V2_API_URL``; ``DAYTONA_API_URL``
    belongs to the 0.x SDK and is never read. There is no region either: pass
    ``region=`` to a create.

    ``request_timeout`` is the total time one ordinary request may take, in
    seconds (default 60; ``0``, or a value above 2,147,483.647 (about 24.8
    days), means no limit). Waits and long polls use their own
    budget, streams are bounded only until their response headers arrive, and
    file transfers have no total bound but fail when no bytes move for that
    long (with ``0``, transfers are not bounded at all). ``max_response_bytes`` (an integer up to 2**53 - 1; ``None`` keeps the 1 GiB default, ``0`` means no limit, and any other value (negative, fractional, boolean, string, too large) is a ``BadRequestError``) caps every response body the SDK holds in memory whole: a
    body over it raises ``PayloadTooLargeError`` (an error response's status
    error for an error status). The in-memory file reads take
    ``max_response_bytes=`` per call too.  ``shared_hub`` controls whether this instance
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
    max_response_bytes: Optional[int] = field(default=None, kw_only=True)


@dataclass(frozen=True, kw_only=True)
class Resources:
    """Linux sandbox resources; CPU is cores (1 core = 2 vCPUs), memory and disk are GiB.

    ``cpu``/``memory``/``disk`` are the request: what is reserved and billed as a
    floor. ``cpu_max``/``memory_max``/``disk_max`` are the limits: the boot shape
    and the ceiling, the system cap when omitted. Set each limit equal to its
    request for a fixed shape. Requests below the platform minimums are raised
    to them, so a fixed shape below a minimum is refused. ``cpu_max`` is a
    multiple of 0.5 cores: the sandbox boots ``cpu_max * 2`` vCPUs. A memory or
    disk value must convert to a whole number of MiB (``value * 1024``);
    anything else raises :class:`BadRequestError`.
    """

    cpu: Optional[float] = None
    cpu_max: Optional[float] = None
    memory: Optional[float] = None
    memory_max: Optional[float] = None
    disk: Optional[float] = None
    disk_max: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class MacosResources:
    """macOS sandbox size; each omitted value uses the Mac model's default size.

    ``cpu`` is physical Mac CPU cores (Apple Silicon has no SMT), at least 4.
    ``memory`` is GiB, at least 4; ``disk`` is GiB, at least 80, macOS included.
    Memory and disk must convert to a whole number of MiB (``value * 1024``).
    Omitted values use the model's ``default_size`` (``mac_models.list()``); a
    create from a captured image that omits ``disk`` gets the image's disk size. The size is fixed
    at creation.
    """

    cpu: Optional[int] = None
    memory: Optional[float] = None
    disk: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class PortMapping:
    port: int
    proto: str = "tcp"


@dataclass(frozen=True, kw_only=True)
class VolumeMount:
    """Attach a volume to a sandbox.

    ``mount_path`` is an absolute path with no ``..`` component. The API
    normalizes it (repeated slashes, ``.`` components and a trailing slash are
    removed) and refuses ``/`` and any path on or beneath ``/bin``, ``/boot``,
    ``/dev``, ``/etc``, ``/lib``, ``/lib32``, ``/lib64``, ``/mnt/scratch``,
    ``/overlay``, ``/proc``, ``/sbin``, ``/sys`` or ``/usr``, or one with a
    control character: the create raises
    :class:`~daytona.v2.errors.BadRequestError` (400).
    """

    volume_id: str
    mount_path: str
    subpath: Optional[str] = None
    # Read-only mounts are supported for bucket volumes, not disk volumes.
    read_only: Optional[bool] = None


class NetworkPolicyInput(TypedDict, total=False):
    block_all: bool | None
    allow_cidrs: Sequence[str]
    allow_domains: Optional[Sequence[str]]


@dataclass(frozen=True, kw_only=True)
class NetworkPolicy:
    version: int
    block_all: bool = False
    allow_cidrs: tuple[str, ...] = ()
    allow_domains: Optional[tuple[str, ...]] = None


@dataclass(frozen=True, kw_only=True)
class NetworkPolicyInfo:
    desired: Optional[NetworkPolicy]
    desired_revision: int
    etag: str
    applied: Optional[NetworkPolicy] = None
    applied_revision: Optional[int] = None
    applied_start_id: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class SandboxSecretsInfo:
    secrets: tuple[str, ...]
    desired_revision: int
    etag: str
    applied_revision: Optional[int] = None
    status: Optional[str] = None
    error: Optional[str] = None
    capture_status: Optional[str] = None
    capture_unresolved_count: Optional[int] = None


@dataclass(frozen=True, kw_only=True)
class _SandboxInfo:
    """The metadata a sandbox handle is built from (internal)."""

    id: str
    name: Optional[str] = None
    state: SandboxState = "unknown"
    raw_state: Optional[str] = None
    image: Optional[ImageRef] = None
    snapshot: Optional[SnapshotRef] = None
    image_build_id: Optional[str] = None
    region: Optional[str] = None
    # None until the API reports them (a create or fork handle).
    labels: Optional[Mapping[str, str]] = None
    cpu: Optional[float] = None
    cpu_max: Optional[float] = None
    memory: Optional[float] = None
    memory_max: Optional[float] = None
    disk: Optional[float] = None
    disk_max: Optional[float] = None
    sandbox_class: Optional[Literal["linux", "macos", "unknown"]] = None
    raw_sandbox_class: Optional[str] = None
    mac_boot_mode: Optional[MacBootMode] = None
    persistent: Optional[bool] = None
    spot: Optional[bool] = None
    spot_evicted_at: Optional[datetime] = None
    lifecycle_policy: Optional[SandboxLifecycle] = None
    forked_from: Optional[str] = None
    reservation_id: Optional[str] = None
    user: Optional[str] = None
    error: Optional[str] = None
    #: The server's timestamp strings, verbatim, keyed by their
    #: ``SandboxHandleJSON`` name (``spotEvictedAt``, ``timeoutAt``,
    #: ``idleTimeoutAt``, ``lastActivityAt``): ``to_json()`` writes them back
    #: unchanged, so both SDKs serialize the same instant to the same string.
    wire_timestamps: Mapping[str, str] = field(default_factory=dict)


class ArtifactRefJSON(TypedDict):
    """An image or snapshot reference inside :class:`SandboxHandleJSON`
    (``name`` is present only when known)."""

    id: str
    name: NotRequired[str]


class SandboxLifecycleJSON(TypedDict, total=False):
    """The lifecycle policy inside :class:`SandboxHandleJSON` (timestamps RFC 3339)."""

    timeoutSeconds: int
    onTimeout: str
    timeoutAt: str
    idleExpirySeconds: int
    onIdleTimeout: str
    idleTimeoutAt: str
    lastActivityAt: str
    lastActivitySource: str


class SandboxHandleJSON(TypedDict, total=False):
    """A sandbox handle's metadata as plain JSON: ``Sandbox.to_json()``.

    The same camelCase shape as the TypeScript SDK's ``SandboxHandleJSON``, so a
    snapshot stored by one SDK reads the same in the other. A key is present only
    when the handle knows its value. It carries no URL and no credential, and it
    is a snapshot, not a way back: reconnect with ``client.get(json["sandboxId"])``.
    Timestamps are RFC 3339 strings.
    """

    sandboxId: Required[str]
    name: str
    state: SandboxState
    rawState: str
    image: ArtifactRefJSON
    snapshot: ArtifactRefJSON
    region: str
    labels: Dict[str, str]
    cpu: float
    cpuMax: float
    memory: float
    memoryMax: float
    disk: float
    diskMax: float
    sandboxClass: Literal["linux", "macos", "unknown"]
    rawSandboxClass: str
    persistent: bool
    spot: bool
    spotEvictedAt: str
    lifecyclePolicy: SandboxLifecycleJSON
    forkedFrom: str
    reservationId: str
    user: str
    error: str
    imageBuildId: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageRef:
    id: str
    name: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotRef:
    id: str
    name: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
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
    #: Type-specific settings as read-only JSON (bucket settings and credential secret names).
    options: Optional[Mapping[str, JsonValue]] = None
    used_bytes: Optional[int] = None
    used_bytes_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Secret:
    """A project secret's metadata; the value is write-only and never returned."""

    name: str
    #: The hosts the value may be sent to; ``("*",)`` means any verified host.
    allowed_domains: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    description: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MacResources:
    """A Mac sandbox size: CPU cores (one Mac CPU each), memory and disk in MiB."""

    cores: int
    memory_mib: int
    disk_mib: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MacModel:
    """A registered Mac model (``GET /mac-models``).

    ``memory_mib`` and ``disk_mib`` are the Mac's physical memory and SSD;
    ``sandbox_disk_mib`` is the disk room for sandboxes on one Mac;
    ``max_sandboxes`` how many run at once; ``default_size`` the size a create
    gives each size it omits.
    """

    id: str
    name: str
    cores: int
    memory_mib: int
    disk_mib: int
    sandbox_disk_mib: int
    max_sandboxes: int
    default_size: Optional[MacResources] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MacPrice:
    """What one reservation of a Mac model costs: ``period_cents`` per ``period_seconds``."""

    cents_per_hour: float
    period_cents: int
    period_seconds: int
    #: Lowercase ISO 4217 code.
    currency: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MacCatalogModel:
    """One Mac model in a region: ``available`` free Macs (advisory) and its ``price`` (``None`` without pricing)."""

    model: MacModel
    available: int
    price: Optional[MacPrice] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class MacCatalogRegion:
    id: str
    #: The model a reservation that names none buys.
    default_model: str
    models: tuple[MacCatalogModel, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MacCatalog:
    """``GET /mac-catalog``: per region, the default model and each model's free Macs and price."""

    regions: tuple[MacCatalogRegion, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MacReservation:
    id: str
    org_id: str
    project_id: str
    region: str
    status: MacReservationStatus
    raw_status: str
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


@dataclass(frozen=True, slots=True, kw_only=True)
class MacReservationBatch:
    """``POST /reservations/bulk``: ``requested`` (the request's count), ``reserved``, and one reservation per reserved Mac."""

    requested: int
    reserved: int
    reservations: tuple[MacReservation, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectBudget:
    """The monthly spend budget of a project, in US cents at list price.

    ``monthly_limit_cents`` is ``None`` when no budget is set. ``spent_cents``
    and ``evaluated_at`` are ``None`` until the first evaluation of the period;
    ``exceeded_at`` is set once the project reaches its budget and ``stop_at``
    is when its grace period ends.
    """

    #: The first day (UTC) of the current budget period.
    period_start_date: Optional[date] = None
    monthly_limit_cents: Optional[int] = None
    spent_cents: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    evaluated_at: Optional[datetime] = None
    exceeded_at: Optional[datetime] = None
    stop_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageLimit:
    """A limit and the usage counted against it; ``limit`` ``None`` means unlimited."""

    limit: Optional[int]
    usage: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MemberLimits:
    limit: Optional[int]
    usage: int
    #: Invitations that hold a member slot until accepted or declined.
    pending_invitations: int


@dataclass(frozen=True, slots=True, kw_only=True)
class SandboxLimits:
    concurrent: UsageLimit
    total: UsageLimit


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageBuildLimits:
    concurrent: UsageLimit


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageLimits:
    volumes: UsageLimit
    images: UsageLimit
    snapshots: UsageLimit


@dataclass(frozen=True, slots=True, kw_only=True)
class SandboxResourceLimits:
    """Per-sandbox maximums for Linux sandboxes: ``cores_max`` in cores and ``memory_max_mib``."""

    max_cores: float
    max_memory_mib: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OrganizationLimits:
    """An organization's limits and current usage (``get_limits()``)."""

    sandboxes: SandboxLimits
    image_builds: ImageBuildLimits
    storage: StorageLimits
    secrets_per_project: UsageLimit
    api_keys_per_project: UsageLimit
    api_keys_per_organization: UsageLimit
    sandbox_resources: Optional[SandboxResourceLimits] = None
    #: Mac reservations the organization holds: pending, active or ending. Renewals do not count again.
    mac_reservations: Optional[UsageLimit] = None
    #: Omitted for credentials scoped to a project.
    members: Optional[MemberLimits] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectApiKeyContext:
    """The project and organization an API key acts in."""

    org_id: str
    org_name: str
    project_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectApiKey:
    """An API key's metadata (``current_api_key()``); the token itself is never returned."""

    id: str
    name: str
    project_id: str
    project_permissions: tuple[str, ...]
    created_at: datetime
    context: ProjectApiKeyContext
    created_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    #: The recognizable leading and trailing token characters, when available.
    key_prefix: Optional[str] = None
    key_suffix: Optional[str] = None
    service_account_id: Optional[str] = None
    service_account_name: Optional[str] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditActionGroup:
    """The actions the audit log can record against one target type."""

    target_type: str
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditActionList:
    targets: tuple[AuditActionGroup, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class RegistryImageSource:
    """An image imported from a registry reference."""

    ref: str
    type: Literal["registry"] = "registry"


@dataclass(frozen=True, kw_only=True, slots=True)
class DockerfileImageSource:
    """An image built from a Dockerfile and its uploaded build context."""

    dockerfile: str
    type: Literal["dockerfile"] = "dockerfile"


@dataclass(frozen=True, kw_only=True, slots=True)
class NamedImageSource:
    """An image that names another image."""

    name: str
    type: Literal["named"] = "named"


@dataclass(frozen=True, kw_only=True, slots=True)
class ManifestImageSource:
    """An image described by a named manifest."""

    name: str
    type: Literal["manifest"] = "manifest"


@dataclass(frozen=True, kw_only=True, slots=True)
class UnknownImageSource:
    """A source kind this SDK release does not know (a later API added it).

    ``raw_type`` is the wire ``source.type``; the source's other fields are not
    read. Handle it as "some other source" rather than treating it as an error.
    """

    raw_type: str
    type: Literal["unknown"] = "unknown"


ImageSource = Union[
    RegistryImageSource,
    DockerfileImageSource,
    NamedImageSource,
    ManifestImageSource,
    UnknownImageSource,
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Image:
    id: str
    name: Optional[str]
    # pending | building | ready | failed (open): the pipeline state of the
    # artifact at its current `format_version`.
    status: ImageStatus
    region: str
    anonymous: bool
    retention: ArtifactRetention
    raw_retention: str
    #: The sandbox class the image boots (open vocabulary).
    sandbox_class: str
    available_in: tuple[str, ...]
    #: How the image was created (open vocabulary: registry, dockerfile, capture, ...).
    origin: str
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    expires_at: Optional[datetime] = None
    source: Optional[ImageSource] = None
    bytes: Optional[int] = None
    layer_count: Optional[int] = None
    # The image format: 1 for images imported from Daytona v0, 2 for the
    # current format. None when omitted by the API.
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
    # The disk size in MiB that a sandbox created from this image gets when
    # the create omits `disk_mib`. Set for macOS snapshots; None for every
    # other image or when unavailable.
    disk_mib: Optional[int] = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    id: str
    name: str
    status: str
    region: str
    kind: SnapshotKind
    raw_kind: str
    sandbox_class: SnapshotClass
    raw_sandbox_class: str
    available_in: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    bytes: Optional[int] = None
    memory_bytes: Optional[int] = None
    sandbox_id: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
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
    timeout_at: Optional[datetime] = None
    idle_expiry_seconds: Optional[int] = None
    on_idle_timeout: Optional[str] = None
    idle_timeout_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    last_activity_source: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class TerminalSize:
    """PTY terminal dimensions, in character cells. A size you pass has
    ``cols`` and ``rows`` each an integer from 1 to 65535 (checked before the
    request); a Windows sandbox takes at most 32767 and refuses a larger one
    with :class:`~daytona.v2.errors.BadRequestError`. A size the sandbox
    reports (a process's ``terminal``) may be any positive 32-bit integer.
    """

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
    tag: Optional[str] = None
    """The tag the process was created with; a session command carries its session's tag. Never changes."""
    pid: Optional[int] = None
    session_id: Optional[str] = None
    shell_command: Optional[str] = None
    argv: Optional[tuple[str, ...]] = None
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


@dataclass(frozen=True, kw_only=True)
class DockerStatus:
    owner: DockerOwner
    raw_owner: str
    state: DockerState
    raw_state: str
    process_id: Optional[str] = None
    reason: Optional[str] = None


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

    items: tuple[LogFrame, ...]
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
    files: tuple[BulkWriteFile, ...]
    """The files written."""
    errors: tuple[str, ...]
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
    errors: tuple[BulkReadError, ...] = ()


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
    file_status: tuple[GitFileStatus, ...]


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
    logs: tuple[LogFrame, ...] = ()
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
    traceback: tuple[str, ...] = ()


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
    results: tuple[InterpreterOutput, ...] = ()
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
        /,
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


@dataclass(frozen=True, kw_only=True)
class PreviewEndpoint:
    """A sandbox HTTP preview endpoint."""

    port: int
    public: bool
    url: str
    created_at: Optional[datetime] = None


@dataclass(frozen=True, kw_only=True)
class PreviewLink:
    """Preview URL plus an access token for one sandbox port.

    Send ``token`` in the ``X-Daytona-Preview-Token`` header to reach a private
    endpoint; without a credential it answers 401. Public endpoints set
    ``token`` to ``""`` and need no credential.
    ``expires_at`` is set when the token's expiry is known, else ``None``.

    Example — curl a private endpoint::

        link = await sandbox.ports.preview_link(8080)
        # curl -H "X-Daytona-Preview-Token: <link.token>" <link.url>

    For a shareable URL that works in a browser without extra headers, use
    :meth:`~daytona.v2.ports.AsyncPortsService.signed_preview_link` instead.
    """

    url: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: Optional[datetime] = None


@dataclass(frozen=True, kw_only=True)
class SignedPreviewLink:
    """Self-contained signed URL for one sandbox port.

    The token is embedded in the hostname — no extra headers or credentials are
    needed. The link expires at ``expires_at``, which the contract requires
    (a response without it is a ``BadRequestError``). Revoke it early with
    :meth:`~daytona.v2.ports.AsyncPortsService.expire_signed_preview_link`,
    which revokes every signed preview link on the sandbox.
    """

    url: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class SandboxAccessToken:
    """A short-lived per-sandbox access token (``POST …/access-token``).

    ``access_token`` is a bearer for sandbox services (toolbox, SSH,
    desktop, private previews) and for the self-service sandbox routes named
    by ``operations`` (``sandbox:stop`` / ``sandbox:pause`` /
    ``sandbox:delete``). It expires at ``expires_at`` and cannot
    be refreshed — mint a fresh one with
    :meth:`~daytona.v2.sandbox.AsyncSandbox.create_access_token` instead. ``ports``
    is the granted private-preview port set; ``None`` means every port.
    """

    access_token: str = field(repr=False)
    token_type: str
    expires_at: datetime
    operations: tuple[str, ...]
    ports: Optional[tuple[int, ...]] = None


SearchSort = Literal[
    "created_at", "updated_at", "last_activity_at", "name", "cores", "memory_mib", "disk_mib"
]
SearchOrder = Literal["asc", "desc"]
SearchTimestamp = Union[str, datetime]
"""A search bound: an RFC 3339 timestamp without a leap second (year 0001-9999,
a real calendar date), checked and sent exactly as written; or a ``datetime``,
sent in UTC, so its UTC instant must fall in years 0001-9999 (a naive one,
whose ``utcoffset()`` is ``None``, is taken as UTC)."""


@dataclass(frozen=True, kw_only=True)
class SandboxSearchFilters:
    """The ``GET /sandboxes/search`` filters (``Daytona.search()``).

    ``states``, ``sandbox_class`` and ``region`` take a single value or a list
    (any-of); ``states`` are the SDK's sandbox states (``started``,
    ``stopped``, ...), sent as the API's status names. ``labels`` is a
    containment match; timestamp starts are inclusive and ends are exclusive.
    """

    q: Optional[str] = None
    states: Optional[Union[SearchSandboxState, Sequence[SearchSandboxState]]] = None
    sandbox_class: Optional[Union[SandboxClass, Sequence[SandboxClass]]] = None
    region: Optional[Union[str, Sequence[str]]] = None
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
class SearchSandboxesOptions(SandboxSearchFilters):
    """Every ``Daytona.search()`` parameter (``GET /sandboxes/search``)."""

    name_prefix: Optional[str] = None
    id_prefix: Optional[str] = None
    cursor: Optional[str] = None
    limit: Optional[int] = None


@dataclass(frozen=True, kw_only=True)
class ListSandboxesOptions:
    """Every ``Daytona.list()`` / ``iter()`` parameter: one page of sandboxes."""

    cursor: Optional[str] = None
    limit: Optional[int] = None


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
