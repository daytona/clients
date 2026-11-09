# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import builtins
import dataclasses
import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import (
    AsyncIterator,
    Callable,
    Dict,
    Final,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Union,
)
from urllib.parse import quote

import anyio
from typing_extensions import Self

from . import _clock
from ._api_core import (
    expect_audit_actions,
    expect_current_api_key,
    expect_organization_limits,
    expect_project_budget,
    expect_sandbox_detail,
    sandbox_detail_wire,
)
from ._auth import ProjectTokenAuth
from ._core import (
    INT32_MAX,
    MAX_SAFE_INTEGER,
    MAX_WIRE_WAIT_SECONDS,
    WAIT_READ_SLACK_SECONDS,
    RequestSpec,
    artifact_ref_from_wire,
    assert_lifecycle_class_support,
    expect_object,
    finite_number,
    gib_to_mib,
    header_text,
    image_options_body,
    lifecycle_body,
    page_params,
    parse_search_page,
    parse_timestamp,
    path_segment,
    region_selector,
    sandbox_state,
    search_params,
    wait_budget_seconds,
)
from ._events import WaitHub, attach_hub, detach_hub, endpoint_from_headers
from ._gen.api import Sandbox as SandboxDto
from ._gen.api import SandboxListItem
from ._otel import load_opentelemetry_support
from ._transport import _MAX_BOUNDED_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS, AsyncTransport
from .errors import (
    AuthenticationError,
    ClientTimeoutError,
    ConnectionError,
    DaytonaError,
    ImageBuildFailedError,
    SandboxCreateConnectionError,
    SandboxCreateRequestTimeoutError,
    create_error,
)
from .image import ImageBuilder
from .network_policy import serialize_network_policy_input
from .sandbox import (
    AsyncSandbox,
    _scoped_toolbox_url,
    _wait_without_raising_for_its_end,
    sandbox_info_from_id,
)
from .services import (
    AsyncImagesService,
    AsyncMacModelsService,
    AsyncReservationsService,
    AsyncSecretsService,
    AsyncSnapshotsService,
    AsyncVolumesService,
)
from .types import (
    AuditActionList,
    DaytonaConfig,
    Image,
    ImageRef,
    JsonObject,
    JsonValue,
    LifecyclePolicyOptions,
    ListResult,
    MacosResources,
    NetworkPolicyInput,
    OrganizationLimits,
    PortMapping,
    ProjectApiKey,
    ProjectBudget,
    Resources,
    SandboxClass,
    SandboxEventsConfig,
    SearchOrder,
    SearchResult,
    SearchSandboxesOptions,
    SearchSandboxState,
    SearchSort,
    SearchTimestamp,
    SnapshotRef,
    VolumeMount,
    _SandboxInfo,
)

CreateConfigValue = (
    JsonValue
    | dict[str, str]
    | Resources
    | LifecyclePolicyOptions
    | Sequence[PortMapping]
    | Sequence[VolumeMount]
    | Sequence[str]
    | NetworkPolicyInput
    | None
)
CreateConfig = dict[str, CreateConfigValue]
CreateBody = JsonObject


# The default Daytona Cloud v2 API. `DAYTONA_V2_API_URL` (a regional endpoint
# or a test environment) selects another one; `DAYTONA_API_URL` belongs to the
# 0.x SDK.
DEFAULT_V2_API_URL: Final[str] = "https://api.daytona.com"
CLOSE_GRACE_SECONDS: Final[float] = 5.0
_DAYTONA_OTEL_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true"})


_DETAIL_FALLBACK_CONCURRENCY: Final = 8


@dataclass(frozen=True, slots=True)
class _ResolvedConfig:
    api_url: str = field(repr=False)
    api_key: Optional[str] = field(repr=False)
    jwt_token: Optional[str] = field(repr=False)
    project_id: Optional[str]
    otel_enabled: bool
    connection_pool_maxsize: Optional[int]
    request_timeout: float


# The `network_policy` default of the create methods: its identity means
# "omitted" (the API applies no policy field), `None` sends an explicit null,
# and a mapping sends that policy.
_UNSET_NETWORK_POLICY: Final[NetworkPolicyInput] = {}


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class AsyncDaytona:
    """The v2 Daytona client."""

    def __init__(self, config: Optional[DaytonaConfig] = None, *, _source: str = "sdk-python-async") -> None:
        resolved = _resolve_config(config)
        self._api_key = resolved.api_key
        self._key_org_id: Optional[str] = None
        self._key_org_lock = anyio.Lock()
        self._api_url = resolved.api_url
        # No region in config: a create picks its region with `region=`; the
        # events hub key carries no region either.
        self._target = "local"
        self._request_timeout = resolved.request_timeout
        shared_hub = config.shared_hub if config is not None else True
        events_config = _resolve_events_config(config.events if config is not None else None)
        # `_source` is the X-Daytona-Source label; Daytona passes "sdk-python"
        # because it drives this class through a bridge and would otherwise be
        # indistinguishable from async callers in usage tracking.
        otel = load_opentelemetry_support() if resolved.otel_enabled else None
        auth = None
        # The shared-hub identity: the API key, or for JWT auth the project plus
        # a digest of the token. The token must be part of it (two users' JWTs
        # for one project must not share a hub minting with the first one's
        # credentials), but the registry key never contains the raw JWT (the
        # hub itself must hold the client's auth to mint tickets).
        self._hub_identity = resolved.api_key or (
            f"jwt:{resolved.project_id}:{hashlib.sha256(resolved.jwt_token.encode()).hexdigest()}"
            if resolved.jwt_token
            else ""
        )
        if resolved.jwt_token:
            auth = ProjectTokenAuth(
                api_url=self._api_url,
                jwt_token=resolved.jwt_token,
                project_id=resolved.project_id or "",
                source=_source,
                otel=otel,
                request_timeout=resolved.request_timeout,
            )
        self._api = AsyncTransport(
            self._api_url,
            self._api_key,
            source=_source,
            request_timeout=self._request_timeout,
            connection_pool_maxsize=resolved.connection_pool_maxsize,
            auth=auth,
            otel=otel,
        )
        self._auth = auth
        self._toolbox = AsyncTransport.shared(
            source=_source,
            request_timeout=self._request_timeout,
            connection_pool_maxsize=resolved.connection_pool_maxsize,
            auth=auth,
            otel=otel,
        )
        #: Images: build, pull, list, promote, delete, follow build logs.
        self.images = AsyncImagesService(self._api)
        #: Memory snapshots (``Sandbox.create_snapshot()`` makes them).
        self.snapshots = AsyncSnapshotsService(self._api)
        #: Volumes that keep files between sandboxes.
        self.volumes = AsyncVolumesService(self._api)
        #: Project secrets (write-only values).
        self.secrets = AsyncSecretsService(self._api)
        #: Dedicated-Mac reservations, which macOS sandboxes run on.
        self.reservations = AsyncReservationsService(self._api)
        #: The Mac model registry that ``reservations.create(model=...)`` draws from.
        self.mac_models = AsyncMacModelsService(self._api)
        # Hub is attached lazily on the first async call so that the asyncio
        # event loop id (needed for per-loop hub isolation) is always the loop
        # the hub will actually run on — not the calling thread's default loop.
        self._hub: Optional[WaitHub] = None
        self._hub_shared = shared_hub
        self._hub_events_config = events_config
        self._source = _source
        self._closed = False

    async def create(
        self,
        image: Optional[str] = None,
        /,
        *,
        name: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        labels: Optional[Mapping[str, str]] = None,
        resources: Optional[Resources] = None,
        ports: Optional[Sequence[PortMapping]] = None,
        volumes: Optional[Sequence[VolumeMount]] = None,
        secrets: Optional[Sequence[str]] = None,
        spot: Optional[bool] = None,
        user: Optional[str] = None,
        lifecycle_policy: Optional[LifecyclePolicyOptions] = None,
        network_policy: Optional[NetworkPolicyInput] = _UNSET_NETWORK_POLICY,
        cpu_arch: Optional[str] = None,
        cpu_type: Optional[str] = None,
        region: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        wait_seconds: int = 60,
        create_timeout_seconds: Optional[int] = None,
    ) -> AsyncSandbox:
        """Create a Linux sandbox from a registered image name or ``img_…`` id.

        Omitting ``image`` boots the platform default image, the Linux image
        Daytona marks as default (``images.list(platform="true")`` shows
        it with ``default=True``); the returned sandbox's ``image`` names the
        image the server booted. When no default is configured the create
        fails with ``DEFAULT_IMAGE_NOT_CONFIGURED`` (400). An empty or
        whitespace-only ``image`` is still an error, raised before any request.
        Snapshots and image builds have their own methods:
        :meth:`create_from_snapshot`, :meth:`create_from_build`.

        **Waiting.** The server holds this create briefly, so a sandbox that
        reaches ``started`` within that hold returns in one round trip; the SDK
        waits for any remainder of ``wait_seconds`` (whole seconds, default 60;
        ``0`` returns as soon as the API accepts the create).
        ``create_timeout_seconds`` is the server's own budget for reaching
        ``started`` before it deletes the sandbox. ``create()`` never raises
        because of how its wait ended: when the budget runs out, or the sandbox
        reaches ``error``, it returns the handle — check ``sandbox.state``. It
        raises when the request itself fails (validation, authentication,
        limits, :class:`SandboxCreateRequestTimeoutError`,
        :class:`SandboxCreateConnectionError`).

        **Names.** ``name`` is non-unique display metadata and the handle of the
        one-sandbox-per-end-user pattern: a named create stamps
        ``labels["name"] = name`` (:meth:`find_by_name` reads it back) and,
        unless ``idempotency_key`` is given, derives its idempotency key from
        the name. A repeated or retried ``create(image, name="example-user")``
        at one regional API endpoint is therefore a get-or-create: the same
        sandbox comes back, not a second one — until the first is deleted,
        which releases the key. This is permanent behaviour of ``name=``. The
        guarantee is scoped to a regional API endpoint (creates of one end user
        that reach two regional API endpoints make two sandboxes; pin them to
        one region), per class, and exact: a same-name create with different
        arguments is a 409, never a silent replay of the first sandbox's
        parameters. An explicit ``idempotency_key`` always wins.

        **Visible resources.** ``resources`` sizes the sandbox (cores and GiB;
        see :class:`Resources`). Burstable sandboxes start with the burst
        ceiling (``resources.cpu_max`` / ``memory_max``, or the deployment cap
        when omitted), so ``nproc`` and ``/proc/meminfo`` report that ceiling,
        not the guaranteed ``cpu``. Tools that size themselves from the CPU
        count (``pytest -n auto``, ``make -j$(nproc)``) start one worker per
        visible CPU. Pass an explicit worker count when you need the guarantee
        instead of the ceiling.

        ``network_policy`` sets the sandbox's egress policy from its first boot
        (``None`` sends an explicit "no policy"); change it later with
        :meth:`AsyncSandbox.update_network_policy`.

        Creating Linux sandboxes requires Linux access enabled for the organization.
        Otherwise a caller holding ``write:sandboxes`` gets
        ``403`` ``FEATURE_NOT_ENABLED`` (raised as
        :class:`FeatureNotEnabledError`).
        """
        resolved_image = _require_image_source(image) if image is not None else None
        config = self._linux_create_config(
            resolved_image,
            name=name,
            env=env,
            labels=labels,
            resources=resources,
            ports=ports,
            volumes=volumes,
            secrets=secrets,
            spot=spot,
            user=user,
            lifecycle_policy=lifecycle_policy,
            network_policy=network_policy,
            cpu_arch=cpu_arch,
            cpu_type=cpu_type,
            region=region,
            wait_seconds=wait_seconds,
            create_timeout_seconds=create_timeout_seconds,
        )
        return await self._create_with_wait(
            config,
            idempotency_key=idempotency_key,
            sandbox_class="linux",
            image_ref=_source_image_ref(resolved_image) if resolved_image is not None else None,
            snapshot_ref=None,
        )

    def _linux_create_config(
        self,
        image: Optional[str],
        *,
        name: Optional[str],
        env: Optional[Mapping[str, str]],
        labels: Optional[Mapping[str, str]],
        resources: Optional[Resources],
        ports: Optional[Sequence[PortMapping]],
        volumes: Optional[Sequence[VolumeMount]],
        secrets: Optional[Sequence[str]],
        spot: Optional[bool],
        user: Optional[str],
        lifecycle_policy: Optional[LifecyclePolicyOptions],
        network_policy: Optional[NetworkPolicyInput],
        cpu_arch: Optional[str],
        cpu_type: Optional[str],
        region: Optional[str],
        wait_seconds: int,
        create_timeout_seconds: Optional[int],
    ) -> CreateConfig:
        _validate_wait(wait_seconds)  # None is refused, not the default
        config = _merge_create_params(
            route="linux",
            source_kind="image",
            source=image,
            name=name,
            env=_dict_or_none(env, "env"),
            labels=_dict_or_none(labels, "labels"),
            resources=resources,
            ports=ports,
            volumes=volumes,
            secrets=secrets,
            spot=spot,
            user=user,
            lifecycle_policy=lifecycle_policy,
            cpu_arch=cpu_arch,
            cpu_type=cpu_type,
            target=region if region is not None else self._target,
            wait_seconds=wait_seconds,
            create_timeout_seconds=create_timeout_seconds,
        )
        if network_policy is not _UNSET_NETWORK_POLICY:
            config["network_policy"] = network_policy
        return config

    async def create_from_snapshot(
        self,
        snapshot: str,
        /,
        *,
        name: Optional[str] = None,
        labels: Optional[Mapping[str, str]] = None,
        lifecycle_policy: Optional[LifecyclePolicyOptions] = None,
        ports: Optional[Sequence[PortMapping]] = None,
        region: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        wait_seconds: int = 60,
        create_timeout_seconds: Optional[int] = None,
    ) -> AsyncSandbox:
        """Create a Linux sandbox that resumes a memory snapshot (name or ``snap_…`` id).

        The sandbox continues from the snapshot's memory and disk, so its size,
        environment and attachments are the snapshot's own. ``name``,
        ``idempotency_key``, ``wait_seconds`` and ``create_timeout_seconds``
        behave as on :meth:`create`, and like :meth:`create` this never raises
        because of how its wait ended.

        Creating Linux sandboxes requires Linux access enabled for the organization.
        Otherwise a caller holding ``write:sandboxes`` gets
        ``403`` ``FEATURE_NOT_ENABLED`` (raised as
        :class:`FeatureNotEnabledError`).
        """
        resolved_snapshot = _require_snapshot_source(snapshot)
        _validate_wait(wait_seconds)  # None is refused, not the default
        config = _merge_create_params(
            route="linux",
            source_kind="snapshot",
            source=resolved_snapshot,
            name=name,
            labels=_dict_or_none(labels, "labels"),
            lifecycle_policy=lifecycle_policy,
            ports=ports,
            target=region if region is not None else self._target,
            wait_seconds=wait_seconds,
            create_timeout_seconds=create_timeout_seconds,
        )
        return await self._create_with_wait(
            config,
            idempotency_key=idempotency_key,
            sandbox_class="linux",
            image_ref=None,
            snapshot_ref=_source_snapshot_ref(resolved_snapshot),
        )

    async def create_macos(
        self,
        image: str,
        /,
        *,
        resources: Optional[MacosResources] = None,
        name: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        labels: Optional[Mapping[str, str]] = None,
        secrets: Optional[Sequence[str]] = None,
        ports: Optional[Sequence[PortMapping]] = None,
        persistent: Optional[bool] = None,
        lifecycle_policy: Optional[LifecyclePolicyOptions] = None,
        volumes: Optional[Sequence[VolumeMount]] = None,
        reservation: Optional[str] = None,
        auto_renew: Optional[bool] = None,
        region: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        wait_seconds: int = 60,
        create_timeout_seconds: Optional[int] = None,
        network_policy: Optional[NetworkPolicyInput] = _UNSET_NETWORK_POLICY,
    ) -> AsyncSandbox:
        """Create a macOS sandbox on a reserved Mac from a macOS image name.

        ``resources`` sizes the sandbox (:class:`MacosResources`: physical CPU
        cores, at least 4; memory in GiB, at least 4; disk in GiB, at least 80,
        macOS included; memory and disk must convert to whole MiB). Each
        omitted value uses half of the claiming Mac, rounded down, on a model
        that runs two sandboxes, or all of it on a model that runs one. The
        first successful claim fixes the size, which the sandbox reports from
        then on.

        ``persistent``: Stop keeps the disk on the same Mac. Start and resume use that Mac. Delete erases the disk. At reservation end, the disk is saved as an anonymous image before the Mac is erased; a lost Mac saves it if it comes back within 7 days. If a Mac restarts before it is declared lost, a running persistent sandbox comes back stopped with its disk, a running ephemeral sandbox is lost, and stopped and paused sandboxes are unchanged. Create a sandbox from an image to continue on another Mac.

        ``volumes`` attaches disk volumes read-write. A sandbox with volumes
        cannot be persistent; bucket volumes and ``read_only`` are rejected
        with :class:`BadRequestError`.

        ``reservation`` picks the Mac's reservation (``daytona.reservations``):
        ``None`` uses any fitting reservation of the organization in the region (or
        lets the organization's auto-reserve buy one), ``"new"`` buys a reservation of
        the cheapest model that fits.
        A reservation ID targets that reservation at claim. Acceptance does not reserve room; the sandbox can wait for room or time out.
        ``auto_renew`` is allowed only with ``reservation="new"``. With no reservation
        to use it raises :class:`ConflictError` (``NO_ACTIVE_RESERVATION``,
        ``NO_FREE_SLOT``, ``MAC_DISK_FULL``, ``AUTO_RESERVE_LIMIT_REACHED``,
        ``NO_MAC_AVAILABLE``). ``name``, ``idempotency_key``, ``wait_seconds``
        and ``create_timeout_seconds`` behave as on :meth:`create`, and like
        :meth:`create` this never raises because of how its wait ended.

        Creating macOS sandboxes requires macOS access enabled for the organization;
        otherwise the API answers ``403``
        ``FEATURE_NOT_ENABLED`` (raised as :class:`FeatureNotEnabledError`).

        ``secrets`` attaches project secrets by name, as on :meth:`create`.
        Their values are available to HTTPS requests to the secret's allowed
        domains without entering the sandbox. Attaching needs ``read:secrets``.

        ``network_policy`` sets the sandbox's egress policy, as on
        :meth:`create`: it applies from the sandbox's first boot. If the policy
        cannot be enforced, the API answers ``503``. Change it later with
        :meth:`AsyncSandbox.update_network_policy`.

        Find regions and their regional API endpoints with ``GET /regions``;
        discover Mac models and prices with ``mac_models.catalog(region="<region>")``.
        To select a regional API endpoint, set ``DAYTONA_V2_API_URL`` before
        creating the client. An API that does not serve the region answers
        :class:`ConflictError`; the SDK does not reroute.
        """
        resolved_image = _require_image_source(image, "create_macos")
        if auto_renew is not None and reservation != "new":
            raise create_error(
                'macos create takes auto_renew only with reservation="new"', 400, "BAD_REQUEST", "SDK"
            )
        if resources is not None and not isinstance(resources, MacosResources):
            raise create_error("create_macos takes resources=MacosResources(...)", 400, "BAD_REQUEST", "SDK")
        _validate_wait(wait_seconds)  # None is refused, not the default
        config = _merge_create_params(
            route="macos",
            source_kind="image",
            source=resolved_image,
            name=name,
            env=_dict_or_none(env, "env"),
            labels=_dict_or_none(labels, "labels"),
            secrets=secrets,
            ports=ports,
            persistent=persistent,
            volumes=volumes,
            cores=_macos_cores(resources.cpu) if resources is not None else None,
            memory_mib=_macos_mib(resources.memory, axis="memory", floor_mib=4096)
            if resources is not None
            else None,
            disk_mib=_macos_mib(resources.disk, axis="disk", floor_mib=81920)
            if resources is not None
            else None,
            lifecycle_policy=lifecycle_policy,
            reservation=reservation,
            auto_renew=auto_renew,
            target=region if region is not None else self._target,
            wait_seconds=wait_seconds,
            create_timeout_seconds=create_timeout_seconds,
        )
        if network_policy is not _UNSET_NETWORK_POLICY:
            config["network_policy"] = network_policy
        return await self._create_with_wait(
            config,
            idempotency_key=idempotency_key,
            sandbox_class="macos",
            image_ref=_source_image_ref(resolved_image),
            snapshot_ref=None,
        )

    async def create_from_build(
        self,
        builder: ImageBuilder,
        /,
        *,
        name: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        labels: Optional[Mapping[str, str]] = None,
        resources: Optional[Resources] = None,
        ports: Optional[Sequence[PortMapping]] = None,
        volumes: Optional[Sequence[VolumeMount]] = None,
        secrets: Optional[Sequence[str]] = None,
        spot: Optional[bool] = None,
        user: Optional[str] = None,
        lifecycle_policy: Optional[LifecyclePolicyOptions] = None,
        network_policy: Optional[NetworkPolicyInput] = _UNSET_NETWORK_POLICY,
        cpu_arch: Optional[str] = None,
        cpu_type: Optional[str] = None,
        region: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        wait_seconds: int = 60,
        create_timeout_seconds: Optional[int] = None,
        idle_expiry_seconds: Optional[int] = None,
        build_timeout: Optional[float] = None,
    ) -> AsyncSandbox:
        """Build an anonymous image from ``builder``, wait for it, then create the sandbox from it.

        The build submission is an ordinary request, bounded by the client's
        request timeout. The build itself is always awaited, bounded by
        ``build_timeout`` (seconds; ``None``, ``0`` or a value above about
        24.8 days = no deadline). A failed
        build (image status ``failed`` or ``error``) raises
        :class:`ImageBuildFailedError` and a build that outlasts
        ``build_timeout`` raises :class:`RequestTimeoutError`; in both cases
        no sandbox exists. Then the sandbox is created exactly as by
        :meth:`create` with the same keywords — ``wait_seconds`` is the create's
        own wait, and the create never raises because of how that wait ended.

        ``idle_expiry_seconds`` is the anonymous image's idle-retention window
        (see ``images.build``). ``region`` and ``cpu_arch`` apply to both the
        build and the create: a Dockerfile-built image boots only in its own
        region, so it is built where the sandbox will run.
        """
        if not isinstance(builder, ImageBuilder):
            raise create_error("create_from_build() takes an ImageBuilder", 400, "BAD_REQUEST", "SDK")
        _validate_wait(wait_seconds)
        # Every create option is validated before the (billed) build: a caller
        # error must not cost a build first. The image id is not known yet.
        _create_body(
            self._linux_create_config(
                "img_pending",
                name=name,
                env=env,
                labels=labels,
                resources=resources,
                ports=ports,
                volumes=volumes,
                secrets=secrets,
                spot=spot,
                user=user,
                lifecycle_policy=lifecycle_policy,
                network_policy=network_policy,
                cpu_arch=cpu_arch,
                cpu_type=cpu_type,
                region=region,
                wait_seconds=wait_seconds,
                create_timeout_seconds=create_timeout_seconds,
            )
        )
        _resolve_idempotency_key(idempotency_key, name)
        image_options_body(idle_expiry_seconds=idle_expiry_seconds, region=region, cpu_arch=cpu_arch)
        # 0, None or above the timer maximum (a huge integer included) is no limit.
        if build_timeout is not None:
            build_timeout = wait_budget_seconds(build_timeout, name="build_timeout") or None
        build_region = region_selector(region if region is not None else self._target)

        def build_timed_out(cause: Optional[BaseException] = None) -> DaytonaError:
            error = create_error("image build did not finish before timeout", 408, "REQUEST_TIMEOUT", "SDK")
            error.__cause__ = cause
            return error

        image = await self.images.build(
            builder,
            idle_expiry_seconds=idle_expiry_seconds,
            region=build_region,
            cpu_arch=cpu_arch,
        )
        # The build budget starts once the submission (an ordinary request) is accepted.
        deadline = None if not build_timeout else _clock.monotonic() + build_timeout
        while image.status not in ("ready", "failed", "error"):
            remaining = None if deadline is None else deadline - _clock.monotonic()
            if remaining is not None and remaining <= 0:
                raise build_timed_out()
            poll_started = _clock.monotonic()
            try:
                image = await self.images.get(image.id, wait="ready", timeout=remaining)
            except DaytonaError as error:
                # Only the client's own deadline is the build timeout; a server
                # 408 keeps its source, as in TypeScript.
                own_timeout = error.source == "SDK" and error.code == "REQUEST_TIMEOUT"
                # The artifact wait's own timeout (not a client read timer) is
                # always this deadline running out: the wait shares it.
                wait_ran_out = not isinstance(error, ClientTimeoutError) or _clock.monotonic() >= (
                    deadline or 0
                )
                if own_timeout and deadline is not None and wait_ran_out:
                    raise build_timed_out(error) from error
                raise
            if image.status not in ("ready", "failed", "error"):
                # A server that answers the long poll early is not polled in a loop.
                pause = _BUILD_POLL_INTERVAL_S - (_clock.monotonic() - poll_started)
                if deadline is not None:
                    pause = min(pause, deadline - _clock.monotonic())
                if pause > 0:
                    await asyncio.sleep(pause)
        if image.status in ("failed", "error"):
            raise ImageBuildFailedError(
                image.error or f"image build {image.id} failed",
                status_code=409,
                code="IMAGE_BUILD_FAILED",
                source="SDK",
                image_id=image.id,
                build_error=image.error,
            )
        return await self.create(
            image.id,
            name=name,
            env=env,
            labels=labels,
            resources=resources,
            ports=ports,
            volumes=volumes,
            secrets=secrets,
            spot=spot,
            user=user,
            lifecycle_policy=lifecycle_policy,
            network_policy=network_policy,
            cpu_arch=cpu_arch,
            cpu_type=cpu_type,
            region=region,
            idempotency_key=idempotency_key,
            wait_seconds=wait_seconds,
            create_timeout_seconds=create_timeout_seconds,
        )

    async def _create_with_wait(
        self,
        config: CreateConfig,
        *,
        idempotency_key: Optional[str],
        sandbox_class: SandboxClass,
        image_ref: ImageRef | None,
        snapshot_ref: SnapshotRef | None,
    ) -> AsyncSandbox:
        body = _create_body(config)
        path = _create_path(config)
        create_key = _resolve_idempotency_key(idempotency_key, config.get("name"))
        target = str(body["region_id"]) if isinstance(body.get("region_id"), str) else None
        wait_budget = _create_wait_budget(config)
        # The create response first associates the sandbox id with its
        # regional API endpoint for the events hub.
        created_endpoints: list[str] = []

        def _capture_endpoint(headers: Mapping[str, str]) -> None:
            endpoint = endpoint_from_headers(headers)
            if endpoint:
                created_endpoints.append(endpoint)

        # A create that asks the server to hold is a long poll: the hold plus
        # read slack is never cut by a shorter request timeout. No limit stays
        # no limit; a create with wait_seconds=0 is an ordinary request.
        root_timeout = getattr(self._api, "request_timeout_seconds", 0) or 0
        # A budget above the timer maximum is no limit (compared as an integer,
        # before any float arithmetic): no client deadline on the POST either.
        unbounded = wait_budget > MAX_WIRE_WAIT_SECONDS
        hold_timeout: Optional[float]
        if wait_budget <= 0 or root_timeout <= 0:
            hold_timeout = None
        elif unbounded:
            hold_timeout = 0.0
        else:
            hold_timeout = max(root_timeout, wait_budget + WAIT_READ_SLACK_SECONDS)
        try:
            data = expect_object(
                await self._api.send(
                    RequestSpec(
                        "POST",
                        path,
                        json_body=body,
                        timeout=hold_timeout,
                        headers={
                            "Idempotency-Key": create_key,
                            **({"x-daytona-region": target} if target is not None else {}),
                        },
                        on_headers=_capture_endpoint,
                    )
                )
            )
        except ClientTimeoutError as error:
            raise SandboxCreateRequestTimeoutError(create_key) from error
        except ConnectionError as error:
            raise SandboxCreateConnectionError(create_key, error.message) from error
        raw_id = data.get("id")
        if not isinstance(raw_id, str) or not raw_id:
            # The contract requires the new sandbox's id; nothing can follow without it.
            raise create_error("sandbox create response is missing the sandbox id", 400, "BAD_REQUEST", "SDK")
        sandbox_id = raw_id
        access_token = str(data.get("access_token")) if isinstance(data.get("access_token"), str) else None
        # Tokens with no reported expiry are used as-is until a 401.
        access_token_expires_at = parse_timestamp(data.get("access_token_expires_at"))
        create_toolbox_base = (
            str(data.get("toolbox_proxy_url")) if isinstance(data.get("toolbox_proxy_url"), str) else None
        )
        raw_status = str(data.get("status") or "")
        # The handle holds only what the response carries, plus an `img_` /
        # `snap_` id the caller passed (a name is never an id), and the class
        # only when the call fixes it (create_macos); the rest is unknown until
        # a detail read. The response's `image` is canonical when present.
        booted = artifact_ref_from_wire(data.get("image"))
        info = _SandboxInfo(
            id=sandbox_id,
            name=str(data.get("name")) if isinstance(data.get("name"), str) else None,
            state=sandbox_state(raw_status),
            raw_state=raw_status,
            image=ImageRef(id=booted.id, name=booted.name) if booted is not None else image_ref,
            snapshot=snapshot_ref,
            region=target,
            sandbox_class="macos" if sandbox_class == "macos" else None,
            raw_sandbox_class="macos" if sandbox_class == "macos" else None,
            # Read before the early return: a create a Mac claims within its hold
            # answers running with the reservation.
            reservation_id=str(data.get("reservation_id"))
            if isinstance(data.get("reservation_id"), str)
            else None,
        )
        sandbox = self._sandbox_handle(
            info,
            toolbox_base_url=create_toolbox_base,
            access_token=access_token,
            access_token_expires_at=access_token_expires_at,
            access_token_caller_supplied=False,
            hub=self._acquire_hub() if _on_event_loop() else None,
            hub_provider=self._acquire_hub,
        )
        if created_endpoints:
            sandbox._learn_endpoint(created_endpoints[-1])
        if sandbox.state == "started" or wait_budget == 0:
            return sandbox
        # Read as TypeScript reads it, never refused (the sandbox exists): a
        # finite number, a negative one clamped to 0; anything else is 0.
        raw_wait_cap = data.get("wait_cap_seconds")
        wait_cap = max(0.0, float(raw_wait_cap)) if finite_number(raw_wait_cap) else 0.0
        # No limit stays no limit (0) after the server's hold.
        remaining = 0 if unbounded else max(0, wait_budget - wait_cap)
        if remaining == 0 and not unbounded:
            return sandbox
        # Wait through successive long-poll responses until the remaining
        # budget is spent. A spent budget or a terminal state hands back the
        # sandbox as it is.
        await _wait_without_raising_for_its_end(sandbox, ["started"], remaining)
        return sandbox

    def _sandbox_by_id(self, sandbox_id: str) -> AsyncSandbox:
        """A metadata-less handle for the by-id verbs (``start``, ``stop``,
        ``delete``, …): no toolbox URL, no fetch."""
        return self._sandbox_handle(
            sandbox_info_from_id(sandbox_id),
            access_token_caller_supplied=False,
            hub=self._acquire_hub() if _on_event_loop() else None,
            hub_provider=self._acquire_hub,
        )

    async def get(
        self, sandbox_id: str, *, toolbox_url: Optional[str] = None, access_token: Optional[str] = None
    ) -> AsyncSandbox:
        """Fetch a sandbox by id.

        ``toolbox_url`` overrides the toolbox URL the API returns.
        The SDK
        sends this sandbox's toolbox credential there (the API key, a project
        token, or a minted sandbox token), so pass only a URL you trust —
        never one read from stored or user-supplied data.

        ``access_token`` is a sandbox access token the handle sends to sandbox
        services instead of minting its own; the handle never replaces it.

        Both are checked before any request: ``toolbox_url`` must be a
        non-blank string, and ``access_token`` (sent as a bearer credential)
        non-empty printable ASCII with no surrounding whitespace, never
        stripped; ``None`` omits either.
        """
        # A blank id would read the sandbox list: refused before any request.
        segment = path_segment(sandbox_id, "sandbox_id")
        _check_connect_options(toolbox_url, access_token)
        return self._sandbox_from_sandbox(
            sandbox_detail_wire(await self._api.send(RequestSpec("GET", f"/sandboxes/{segment}"))),
            toolbox_url=toolbox_url,
            access_token=access_token,
            access_token_caller_supplied=access_token is not None,
        )

    async def find_by_name(self, name: str, /, *, region: Optional[str] = None) -> tuple[AsyncSandbox, ...]:
        """Sandboxes created with ``name=name``, newest first; ``()`` when none.

        Exact match on the ``labels["name"]`` every ``create*(name=…)`` stamps,
        read through sandbox search. Names are not unique, so this is a tuple —
        every match, read page by page: the one list method that is not a
        single page. A get-or-create caller takes ``[0]`` when it is non-empty
        and calls ``create(..., name=name)`` otherwise — and because that create
        derives the same key as the first one, a lost race between two such
        callers still yields one sandbox. A new sandbox becomes searchable
        shortly after its create. Sandboxes that are ``destroyed`` or
        ``destroying`` are left out. A deployment without search raises
        :class:`SearchNotConfiguredError` (501).

        ``region`` scopes the read the way it scopes a create: it is sent as
        the same ``x-daytona-region`` selector ``create*()`` sends, and
        ``"local"`` (or omitting it) means the default region — so the
        lookup uses the same regional API endpoint as the named create.
        """
        if not isinstance(name, str) or not name.strip():
            raise create_error("name must be a non-blank string", 400, "BAD_REQUEST", "SDK")
        # The same selector create*() sends: blank or "local" is the default region.
        selector = region_selector(region if region is not None else self._target)
        headers: Dict[str, str] = {"x-daytona-region": selector} if selector is not None else {}
        found: list[AsyncSandbox] = []
        params: Dict[str, JsonValue] = {"labels": json.dumps({_NAME_LABEL: name}), "limit": 100}
        seen_cursors: set[str] = set()
        while True:
            payload = await self._api.send(
                RequestSpec("GET", "/sandboxes/search", params=dict(params), headers=headers)
            )
            items, next_cursor, _, _, _ = parse_search_page(payload, expect_sandbox_detail)
            live = [item for item in items if item.get("status") not in ("deleted", "deleting")]
            found.extend(await self._sandboxes_from_sandboxes(live))
            if not next_cursor or next_cursor in seen_cursors:
                return tuple(found)
            seen_cursors.add(next_cursor)
            params["cursor"] = next_cursor

    async def list_audit_actions(self) -> AuditActionList:
        """Return the actions the audit log can record for each target type."""
        return expect_audit_actions(await self._api.send(RequestSpec("GET", "/audit/actions")))

    async def current_api_key(self) -> ProjectApiKey:
        """Describe the API key this client authenticates with.

        Returns its name, masked display parts, the project and organization it
        acts in, its permissions, expiry and last use. The token itself is
        never returned.
        """
        return expect_current_api_key(await self._api.send(RequestSpec("GET", "/api-keys/current")))

    async def get_project_budget(self) -> ProjectBudget:
        """Return the monthly spend budget of the API key's project.

        An unset budget is returned with nullable limit and period fields. The key needs the
        ``read:spending`` permission. Spend is calculated at list price, before
        credits, and lags usage by several minutes. While the project is over
        its budget, creating or starting a sandbox in it raises
        ``ProjectBudgetExceededError``.
        """
        return expect_project_budget(await self._api.send(RequestSpec("GET", "/usage/budget")))

    async def get_limits(self, *, organization_id: Optional[str] = None) -> OrganizationLimits:
        """Return the organization's limits and current usage.

        A null ``limit`` means unlimited. With an API key the organization
        defaults to the key's own, looked up once and cached; with
        ``jwt_token`` authentication pass ``organization_id``. The credential
        needs the ``read:limits`` permission. Member counts (``members``) are
        not included: they are served to signed-in members only.
        """
        org_id = organization_id if organization_id is not None else await self._key_organization_id()
        return expect_organization_limits(
            await self._api.send(
                RequestSpec("GET", f"/orgs/{path_segment(org_id, 'organization_id')}/limits")
            )
        )

    async def _key_organization_id(self) -> str:
        if self._api_key is None:
            raise create_error(
                "organization_id is required with jwt_token authentication", 400, "BAD_REQUEST", "SDK"
            )
        # One lookup shared by concurrent first calls; a failed lookup leaves
        # the cache empty so the next call retries it.
        if self._key_org_id is None:
            async with self._key_org_lock:
                if self._key_org_id is None:
                    self._key_org_id = str((await self.current_api_key()).context.org_id)
        return self._key_org_id

    async def list(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ListResult[AsyncSandbox]:
        """List sandboxes as one page ``{items, next_cursor}`` (``GET /sandboxes``), newest first.

        Read-your-writes: a sandbox you just created is in the result. Walk
        further pages with :meth:`iter` or the returned cursor. Filtering is
        :meth:`search`'s job.
        """
        params: Dict[str, JsonValue] = page_params(cursor, limit)
        payload = await self._api.send(RequestSpec("GET", "/sandboxes", params=params))
        # The page's shape and every item's sandbox fields, as in TypeScript; the
        # per-item `cursor` is not read, so it is not required.
        if not isinstance(payload, dict):
            raise create_error("sandbox list response is not a JSON object", 400, "BAD_REQUEST", "SDK")
        raw_items = payload.get("sandboxes")
        if not isinstance(raw_items, list):
            raise create_error(
                "sandbox list response is missing the sandboxes array", 400, "BAD_REQUEST", "SDK"
            )
        # Both spellings get the same check: a mistyped cursor would end
        # pagination silently.
        for key in ("next_cursor", "nextCursor"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise create_error(
                    f"sandbox list response has a {key} that is not a string", 400, "BAD_REQUEST", "SDK"
                )
        raw_cursor = payload.get("next_cursor")
        candidate = payload.get("nextCursor")
        next_cursor = (
            raw_cursor if isinstance(raw_cursor, str) else candidate if isinstance(candidate, str) else None
        )
        # Wire objects, strings (timestamps) verbatim.
        items = [sandbox_detail_wire(item) for item in raw_items]
        sandboxes = await self._sandboxes_from_sandboxes(items)
        return ListResult(items=sandboxes, next_cursor=next_cursor)

    async def search(
        self,
        *,
        q: Optional[str] = None,
        name_prefix: Optional[str] = None,
        id_prefix: Optional[str] = None,
        states: Optional[Union[SearchSandboxState, Sequence[SearchSandboxState]]] = None,
        sandbox_class: Optional[Union[SandboxClass, Sequence[SandboxClass]]] = None,
        region: Optional[Union[str, Sequence[str]]] = None,
        image_ref: Optional[str] = None,
        image_ref_prefix: Optional[str] = None,
        labels: Optional[Mapping[str, str]] = None,
        spot: Optional[bool] = None,
        forked_from: Optional[str] = None,
        min_cores: Optional[float] = None,
        max_cores: Optional[float] = None,
        min_memory_mib: Optional[int] = None,
        max_memory_mib: Optional[int] = None,
        min_disk_mib: Optional[int] = None,
        max_disk_mib: Optional[int] = None,
        created_start_at: Optional[SearchTimestamp] = None,
        created_end_at: Optional[SearchTimestamp] = None,
        updated_start_at: Optional[SearchTimestamp] = None,
        updated_end_at: Optional[SearchTimestamp] = None,
        last_activity_start_at: Optional[SearchTimestamp] = None,
        last_activity_end_at: Optional[SearchTimestamp] = None,
        sort: Optional[SearchSort] = None,
        order: Optional[SearchOrder] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> SearchResult[AsyncSandbox]:
        """Search sandboxes across every region (``GET /sandboxes/search``), one page.

        Every parameter is optional and they combine as AND. ``q`` is free text
        over name and image (or an exact id); ``name_prefix``/``id_prefix`` match
        the start of the name or id; ``states``, ``sandbox_class`` and
        ``region`` accept a single value or a list (any-of). ``states`` are the
        SDK's sandbox states (``started``, ``stopped``, ``error``, ...), each
        sent as the API status names it covers; ``"unknown"`` is refused.
        ``labels`` is a containment match; the ``min_*``/``max_*`` bounds are
        inclusive, ``*_start_at`` timestamp bounds are inclusive, and
        ``*_end_at`` bounds are exclusive. Timestamps accept RFC 3339 timestamps
        without a leap second (year 0001-9999, a real calendar date) or
        ``datetime`` objects (naive values are taken as UTC). ``sort`` defaults
        to ``created_at`` and ``order`` to ``desc``; ``limit`` must be an
        integer from 1 to 200, else :class:`BadRequestError` before any
        request. Returns a :class:`SearchResult` whose ``items``
        are the same :class:`AsyncSandbox` handles :meth:`list` returns.

        Consistency: search is **eventually consistent**. Results may be briefly
        stale after a create, update or delete, so a sandbox you
        just created may not be in the result yet — use :meth:`get` or
        :meth:`list` for read-your-writes. Results span every region.

        Cursor: ``next_cursor`` is opaque and bound to the exact filter and sort
        that produced it — presenting it with a different filter or sort is a
        400, never a silent reset. Paging is best-effort under concurrent
        mutation: each page reflects current results, so a sandbox whose sort field
        (``status``, ``name``, ``memory_mib``, ``updated_at``, ...) changes between
        two page fetches can appear on both pages or on neither, and creates or
        deletes mid-walk shift later pages.

        Errors: 400 (:class:`BadRequestError`) for a malformed or mismatched
        cursor, an unknown ``sandbox_class``/``sort`` value, a non-object
        ``labels`` or a ``min`` above its ``max``; 501
        (:class:`SearchNotConfiguredError` with
        ``code == "SEARCH_NOT_CONFIGURED"``) when the API has search
        disabled; 503 (:class:`SearchUnavailableError` with
        ``code == "SEARCH_UNAVAILABLE"``) when search is unavailable. On 501/503
        callers may fall back to :meth:`list`, which does not use search.
        """
        query = SearchSandboxesOptions(
            q=q,
            name_prefix=name_prefix,
            id_prefix=id_prefix,
            states=states,
            sandbox_class=sandbox_class,
            region=region,
            image_ref=image_ref,
            image_ref_prefix=image_ref_prefix,
            labels=labels,
            spot=spot,
            forked_from=forked_from,
            min_cores=min_cores,
            max_cores=max_cores,
            min_memory_mib=min_memory_mib,
            max_memory_mib=max_memory_mib,
            min_disk_mib=min_disk_mib,
            max_disk_mib=max_disk_mib,
            created_start_at=created_start_at,
            created_end_at=created_end_at,
            updated_start_at=updated_start_at,
            updated_end_at=updated_end_at,
            last_activity_start_at=last_activity_start_at,
            last_activity_end_at=last_activity_end_at,
            sort=sort,
            order=order,
            limit=limit,
            cursor=cursor,
        )
        payload = await self._api.send(RequestSpec("GET", "/sandboxes/search", params=search_params(query)))
        items, next_cursor, total, total_relation, raw_total_relation = parse_search_page(
            payload, expect_sandbox_detail
        )
        return SearchResult(
            items=await self._sandboxes_from_sandboxes(items),
            next_cursor=next_cursor,
            total=total,
            total_relation=total_relation,
            raw_total_relation=raw_total_relation,
        )

    def _sandbox_handle(
        self,
        info: _SandboxInfo,
        *,
        direct_toolbox_url: Optional[str] = None,
        toolbox_base_url: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_expires_at: Optional[datetime | str] = None,
        access_token_caller_supplied: bool,
        hub: Optional[WaitHub] = None,
        hub_provider: Optional[Callable[[], Optional[WaitHub]]] = None,
    ) -> AsyncSandbox:
        if direct_toolbox_url is not None:
            toolbox, resolved, base = self._toolbox_transport(
                info.id, direct=direct_toolbox_url, access_token=access_token
            )
        else:
            toolbox, resolved, base = self._toolbox_transport(
                info.id, base=toolbox_base_url, access_token=access_token
            )
        return AsyncSandbox(
            info,
            self._api,
            toolbox,
            _toolbox_owner=self._toolbox,
            _toolbox_url=resolved,
            _access_token=access_token,
            _access_token_expires_at=access_token_expires_at,
            _toolbox_base_url=base,
            _toolbox_token=access_token or self._api_key,
            _direct_toolbox_url=direct_toolbox_url,
            _request_timeout=self._request_timeout,
            _access_token_caller_supplied=access_token_caller_supplied,
            _hub=hub,
            _hub_provider=hub_provider,
        )

    async def iter(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[AsyncSandbox]:
        """Every sandbox, walking :meth:`list` page by page (``limit`` is the page size)."""
        seen_cursors: set[str] = set()
        page_cursor = cursor
        if page_cursor is not None:
            seen_cursors.add(page_cursor)
        while True:
            page = await self.list(cursor=page_cursor, limit=limit)
            for item in page.items:
                yield item
            if (
                page.next_cursor is None
                or page.next_cursor == page_cursor
                or page.next_cursor in seen_cursors
            ):
                return
            seen_cursors.add(page.next_cursor)
            page_cursor = page.next_cursor

    async def start(self, sandbox: Union[AsyncSandbox, str], *, timeout: float = 60) -> None:
        """Start a sandbox by handle or id; see :meth:`AsyncSandbox.start`."""
        await self._resolve_with_hub(sandbox).start(timeout=timeout)

    async def stop(self, sandbox: Union[AsyncSandbox, str], *, timeout: float = 60) -> None:
        """Stop a sandbox by handle or id and wait until it is stopped; see :meth:`AsyncSandbox.stop`."""
        await self._resolve_with_hub(sandbox).stop(timeout=timeout)

    async def pause(self, sandbox: Union[AsyncSandbox, str], *, timeout: float = 60) -> None:
        """Pause a sandbox by handle or id; see :meth:`AsyncSandbox.pause`."""
        await self._resolve_with_hub(sandbox).pause(timeout=timeout)

    async def resume(self, sandbox: Union[AsyncSandbox, str], *, timeout: float = 60) -> None:
        """Resume a sandbox by handle or id; see :meth:`AsyncSandbox.resume`."""
        await self._resolve_with_hub(sandbox).resume(timeout=timeout)

    async def delete(self, sandbox: Union[AsyncSandbox, str], *, timeout: float = 60) -> None:
        """Delete a sandbox by handle or id and wait until it is destroyed; see :meth:`AsyncSandbox.delete`."""
        await self._resolve_with_hub(sandbox).delete(timeout=timeout)

    def _resolve(self, sandbox: Union[AsyncSandbox, str]) -> AsyncSandbox:
        if isinstance(sandbox, AsyncSandbox):
            return sandbox
        # A wrong type or a blank id is refused before any request, as in TypeScript.
        if not isinstance(sandbox, str) or not sandbox.strip():
            raise create_error(
                "sandbox must be a Sandbox or a non-empty sandbox id", 400, "BAD_REQUEST", "SDK"
            )
        # The id goes into request paths: refused like any path value.
        path_segment(sandbox, "sandbox")
        return self._sandbox_by_id(sandbox)

    def _acquire_hub(self) -> Optional[WaitHub]:
        # Only ever called on the event loop (see `_sandbox_by_id()`), whose single
        # thread makes this check-then-set safe.
        if self._closed:
            return None
        if self._hub is not None and self._hub._closed:
            self._hub = None
        if self._hub is None:
            self._hub = attach_hub(
                self._api_url,
                self._api_key,
                self._target,
                shared=self._hub_shared,
                source=self._source,
                config=self._hub_events_config,
                auth=self._auth,
                identity=self._hub_identity,
            )
        return self._hub

    def _resolve_with_hub(self, sandbox: Union[AsyncSandbox, str]) -> AsyncSandbox:
        sb = self._resolve(sandbox)
        if sb._hub is None or sb._hub._closed:
            sb._hub = self._acquire_hub()
        return sb

    async def close(self) -> None:
        """Close HTTP resources."""
        self._closed = True
        hub, self._hub = self._hub, None
        if hub is not None:
            await detach_hub(
                hub,
                self._api_url,
                self._api_key,
                self._target,
                shared=self._hub_shared,
            )
        await self._toolbox.close()
        await self._api.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # Shielded for the same reason as AsyncResponseStream.close(): a body
        # cancelled under an anyio scope would otherwise cancel this await
        # too and leave the connection pools open.
        with anyio.move_on_after(CLOSE_GRACE_SECONDS, shield=True):
            await self.close()

    def _sandbox_from_sandbox(
        self,
        sandbox_data: SandboxDto | SandboxListItem | JsonObject,
        *,
        toolbox_url: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_expires_at: Optional[str] = None,
        access_token_caller_supplied: bool = False,
    ) -> AsyncSandbox:
        if isinstance(sandbox_data, (SandboxDto, SandboxListItem)):
            sandbox_id = sandbox_data.id
            sandbox_base = sandbox_data.toolbox_proxy_url
        else:
            sandbox_id = str(sandbox_data.get("id"))
            raw_sandbox_base = sandbox_data.get("toolbox_proxy_url") or sandbox_data.get("toolboxProxyUrl")
            sandbox_base = str(raw_sandbox_base) if raw_sandbox_base else None
        toolbox, resolved, base = self._toolbox_transport(
            sandbox_id, direct=toolbox_url, base=sandbox_base, access_token=access_token
        )
        return AsyncSandbox._from_api(
            sandbox_data,
            self._api,
            toolbox,
            toolbox_owner=self._toolbox,
            toolbox_url=resolved,
            access_token=access_token,
            toolbox_base_url=base,
            toolbox_token=access_token or self._api_key,
            direct_toolbox_url=toolbox_url,
            request_timeout=self._request_timeout,
            access_token_expires_at=access_token_expires_at,
            # A caller's token (get(access_token=...)) is never replaced; one
            # from an SDK-internal path may be minted over.
            access_token_caller_supplied=access_token_caller_supplied,
            hub=self._acquire_hub(),
            hub_provider=self._acquire_hub,
        )

    async def _sandboxes_from_sandboxes(
        self, sandboxes: Sequence[SandboxDto | SandboxListItem | JsonObject]
    ) -> builtins.list[AsyncSandbox]:
        # Sandboxes lacking a toolbox URL each need a detail read; run them
        # concurrently (bounded) so a degraded page costs a few round trips,
        # not N, without opening a page-sized burst against the API.
        gate = asyncio.Semaphore(_DETAIL_FALLBACK_CONCURRENCY)

        async def build(sandbox_data: SandboxDto | SandboxListItem | JsonObject) -> AsyncSandbox:
            async with gate:
                return await self._sandbox_from_sandbox_with_fetched_toolbox_url(sandbox_data)

        return list(await asyncio.gather(*(build(sandbox_data) for sandbox_data in sandboxes)))

    async def _sandbox_from_sandbox_with_fetched_toolbox_url(
        self,
        sandbox_data: SandboxDto | SandboxListItem | JsonObject,
        *,
        toolbox_url: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_expires_at: Optional[str] = None,
    ) -> AsyncSandbox:
        sandbox = self._sandbox_from_sandbox(
            sandbox_data,
            toolbox_url=toolbox_url,
            access_token=access_token,
            access_token_expires_at=access_token_expires_at,
        )
        if toolbox_url is not None or sandbox._toolbox_proxy_url is not None:
            return sandbox
        try:
            # A detail that breaks the contract is unreadable too: the list item stands.
            detail = sandbox_detail_wire(
                await self._api.send(RequestSpec("GET", f"/sandboxes/{quote(sandbox.id, safe='')}"))
            )
        except DaytonaError:
            # One unreadable item must not fail the page: this handle keeps no
            # toolbox and says so with the explicit error on first toolbox use.
            return sandbox
        return self._sandbox_from_sandbox(
            detail,
            access_token=access_token,
            access_token_expires_at=access_token_expires_at,
        )

    def _toolbox_transport(
        self,
        sandbox_id: str,
        *,
        direct: Optional[str] = None,
        base: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> tuple[Optional[AsyncTransport], Optional[str], Optional[str]]:
        base_url = base.rstrip("/") if base else None
        resolved = _scoped_toolbox_url(sandbox_id, direct=direct, base=base_url)
        transport = self._toolbox.scoped(resolved, access_token or self._api_key) if resolved else None
        return transport, resolved, base_url


def _resolve_config(config: Optional[DaytonaConfig]) -> _ResolvedConfig:
    config = config or DaytonaConfig()
    # Only DAYTONA_V2_API_URL selects the endpoint (DAYTONA_API_URL belongs to
    # the 0.x SDK). Ambient values are trimmed and a blank one is unset; a
    # slash-only URL is unset too.
    api_url = (_ambient("DAYTONA_V2_API_URL") or "").rstrip("/") or DEFAULT_V2_API_URL
    # Explicit values are used as given; an explicitly empty one is an error,
    # never "unset". An explicit credential wins over the ambient environment:
    # jwt_token suppresses DAYTONA_API_KEY and api_key suppresses
    # DAYTONA_JWT_TOKEN, so an exported key never collides with the credential
    # the caller chose.
    for name in ("api_key", "jwt_token", "project_id"):
        value: object = getattr(config, name)
        if value is not None and (not isinstance(value, str) or value == ""):
            raise create_error(f"{name} must be a non-empty string when provided", 400, "BAD_REQUEST", "SDK")
    api_key = (
        config.api_key
        if config.api_key is not None
        else (None if config.jwt_token is not None else _ambient("DAYTONA_API_KEY"))
    )
    jwt_token = (
        config.jwt_token
        if config.jwt_token is not None
        else (None if config.api_key is not None else _ambient("DAYTONA_JWT_TOKEN"))
    )
    if api_key and jwt_token:
        raise create_error(
            "exactly one of api_key or jwt_token must be configured",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    if not api_key and not jwt_token:
        raise AuthenticationError(
            "api_key or jwt_token is required", status_code=401, code="UNAUTHORIZED", source="SDK"
        )
    project_id = config.project_id if config.project_id is not None else _ambient("DAYTONA_PROJECT_ID")
    if jwt_token and not project_id:
        raise create_error("jwt_token requires project_id", 400, "BAD_REQUEST", "SDK")
    otel_enabled = (
        config.otel_enabled if config.otel_enabled is not None else _env_truthy("DAYTONA_OTEL_ENABLED")
    )
    return _ResolvedConfig(
        api_url=api_url,
        api_key=api_key,
        jwt_token=jwt_token,
        project_id=project_id,
        otel_enabled=otel_enabled,
        connection_pool_maxsize=_resolve_connection_pool_maxsize(config.connection_pool_maxsize),
        request_timeout=_resolve_request_timeout(config.request_timeout),
    )


def _ambient(env_var: str) -> Optional[str]:
    """An environment value, trimmed; blank or whitespace-only is unset."""
    return (os.environ.get(env_var) or "").strip() or None


def _resolve_request_timeout(value: Optional[float]) -> float:
    if value is None:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    seconds = _non_negative_seconds("request_timeout", value)
    return 0.0 if seconds > _MAX_BOUNDED_TIMEOUT_SECONDS else float(seconds)


def _non_negative_seconds(name: str, value: object) -> float | int:
    """A finite, non-negative number of seconds, or the SDK's ``BadRequestError``.

    Integers are compared without converting them to float, so an integer
    too large for a float is rejected cleanly rather than overflowing.
    """
    valid = (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value >= 0
        and (isinstance(value, int) or math.isfinite(value))
    )
    if not valid:
        raise create_error(
            f"{name} must be a non-negative number of seconds (0 = no limit)", 400, "BAD_REQUEST", "SDK"
        )
    assert isinstance(value, (int, float))
    return value


# The events hub's timer ceilings, in seconds, matching the TypeScript SDK:
# the longest JavaScript timer, and for the reconcile interval that ceiling less
# its ±10 % jitter. Larger durations clamp to them (unlike the request
# timeout, where a value above the maximum means no limit).
_MAX_EVENT_TIMER_SECONDS: Final = 2_147_483.647
_MAX_RECONCILE_INTERVAL_SECONDS: Final = math.floor(2_147_483_647 / 1.1) / 1000


def _resolve_events_config(config: Optional[SandboxEventsConfig]) -> Optional[SandboxEventsConfig]:
    """Validate the events hub durations at construction and clamp oversized ones.

    ``None`` keeps the hub's default for that duration.
    """
    if config is None:
        return config
    reconcile = _clamped_seconds(
        "events.reconcile_interval", config.reconcile_interval, _MAX_RECONCILE_INTERVAL_SECONDS
    )
    idle = _clamped_seconds("events.idle_close", config.idle_close, _MAX_EVENT_TIMER_SECONDS)
    if reconcile == config.reconcile_interval and idle == config.idle_close:
        return config
    return dataclasses.replace(config, reconcile_interval=reconcile, idle_close=idle)


def _clamped_seconds(name: str, value: Optional[float], ceiling: float) -> Optional[float]:
    """A validated duration, clamped to ``ceiling``; ``None`` stays ``None``."""
    if value is None:
        return None
    seconds = _non_negative_seconds(name, value)
    return ceiling if seconds > ceiling else value


def _env_truthy(env_var: str) -> bool:
    value = os.environ.get(env_var)
    return value is not None and value.strip().lower() in _DAYTONA_OTEL_TRUE_VALUES


def _resolve_connection_pool_maxsize(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise create_error(
            "connection_pool_maxsize must be a positive integer",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return value


def _dict_or_none(value: Optional[Mapping[str, str]], label: str) -> Optional[dict[str, str]]:
    """A string-to-string mapping argument, copied; anything else is refused, never dropped."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise create_error(f"{label} must be a mapping of string to string", 400, "BAD_REQUEST", "SDK")
    return dict(value)


def _merge_create_params(**overrides: CreateConfigValue) -> CreateConfig:
    merged: CreateConfig = {}
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return merged


_MAX_MACOS_RESOURCE_VALUE: Final = 2147483647
#: The least time between two build polls when the server answers early.
_BUILD_POLL_INTERVAL_S: Final = 1.0


# Omitted macOS sizes use the server defaults. Supplied sizes must be within
# the accepted range; GiB converts to MiB exactly, never rounded.
def _macos_cores(cpu: object) -> Optional[int]:
    if cpu is None:
        return None
    if isinstance(cpu, bool) or not isinstance(cpu, int) or not 4 <= cpu <= _MAX_MACOS_RESOURCE_VALUE:
        raise create_error("cpu must be an integer between 4 and 2147483647", 400, "BAD_REQUEST", "SDK")
    return cpu


def _macos_mib(gib: object, *, axis: str, floor_mib: int) -> Optional[int]:
    if gib is None:
        return None
    mib = gib * 1024 if isinstance(gib, (int, float)) and not isinstance(gib, bool) else math.nan
    # An int needs no float check: math.isfinite() and int() raise OverflowError past the float range.
    whole = isinstance(mib, int) or (math.isfinite(mib) and mib == int(mib))
    if not whole or not floor_mib <= mib <= _MAX_MACOS_RESOURCE_VALUE:
        raise create_error(
            f"{axis} must be a finite GiB value that converts to an integer MiB value between {floor_mib} and 2147483647",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    return int(mib)


# The label every ``create*(name=…)`` stamps with the name:
# ``labels == {"name": name, …}``. ``AsyncDaytona.find_by_name`` reads it back.
_NAME_LABEL: Final = "name"

#: Longest idempotency key the API accepts, in characters.
_MAX_IDEMPOTENCY_KEY_LEN: Final = 256
_NAME_KEY_PREFIX: Final = "name:"
_NAME_KEY_HASH_PREFIX: Final = "name:sha256:"


def _idempotency_key_for_name(name: str) -> str:
    """The idempotency key ``create*(name=name)`` derives when none is given.

    ``"name:" + name`` when that is a key the API accepts verbatim — printable
    ASCII (``0x20``–``0x7E``), no surrounding whitespace (the API trims keys
    and HTTP strips header padding, so a padded name would not round-trip),
    at most 256 characters in total — else ``"name:sha256:" + hex(sha256(name))``
    over the UTF-8 bytes. The ``name:`` namespace keeps a derived key from ever
    colliding with a caller's explicit key of the same spelling. Deterministic
    and identical in the TypeScript SDK, so retries from either SDK use the
    same key within a regional API endpoint.
    """
    if not isinstance(name, str):
        raise create_error("name must be a string", 400, "BAD_REQUEST", "SDK")
    literal = _NAME_KEY_PREFIX + name
    if (
        name == name.strip()
        and len(literal) <= _MAX_IDEMPOTENCY_KEY_LEN
        and all(" " <= char <= "~" for char in name)
    ):
        return literal
    return _NAME_KEY_HASH_PREFIX + hashlib.sha256(name.encode("utf-8")).hexdigest()


def _named_create(name: CreateConfigValue | None) -> Optional[str]:
    """The create's name when it names the sandbox (non-blank), else ``None``.

    Mirrors the API, which generates a name for a blank one: a blank name is no
    name, so it neither stamps the label nor derives the key.
    """
    return name if isinstance(name, str) and name.strip() else None


def _check_connect_options(toolbox_url: object, access_token: object) -> None:
    """``get()``'s connect options, as TypeScript checks them: ``None`` is
    omitted; a toolbox URL is a non-blank string; an access token follows the
    header rule (it is sent as a bearer credential), never stripped."""
    if toolbox_url is not None and (not isinstance(toolbox_url, str) or not toolbox_url.strip()):
        raise create_error("toolbox_url must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    if access_token is not None:
        if not isinstance(access_token, str):
            raise create_error("access_token must be a string", 400, "BAD_REQUEST", "SDK")
        header_text(access_token, "access_token")


def _resolve_idempotency_key(idempotency_key: Optional[str], name: CreateConfigValue | None = None) -> str:
    if idempotency_key is not None:
        if not isinstance(idempotency_key, str):
            raise create_error("idempotency_key must be a string", 400, "BAD_REQUEST", "SDK")
        return header_text(idempotency_key, "idempotency_key")
    named = _named_create(name)
    if named is not None:
        return _idempotency_key_for_name(named)
    return str(uuid.uuid4())


def _labels_with_name(config: CreateConfig) -> dict[str, str] | None:
    """The create's labels with the name stamped as ``labels["name"]``.

    An explicit ``labels["name"]`` that disagrees with ``name=`` is a
    contradiction the SDK refuses rather than resolves silently: the label IS
    the name :meth:`AsyncDaytona.find_by_name` matches on.
    """
    labels = _string_map(config.get("labels"))
    named = _named_create(config.get("name"))
    if named is None:
        return labels
    stamped = dict(labels or {})
    existing = stamped.get(_NAME_LABEL)
    if existing is not None and existing != named:
        raise create_error(
            f"labels[{_NAME_LABEL!r}] ({existing!r}) disagrees with name= ({named!r}); "
            "the name label is stamped from name=, pass one or the other",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    stamped[_NAME_LABEL] = named
    return stamped


def _create_body(config: CreateConfig) -> CreateBody:
    resources = config.get("resources")
    route = _create_route(config)
    source_kind = _create_source_kind(config)
    # Only a Linux image create may omit its source: the server boots the
    # platform default image. A present but blank source is always an error.
    source_omitted = "source" not in config and route == "linux" and source_kind == "image"
    source = str(config.get("source") or "")
    if not source_omitted and not source.strip():
        raise create_error(f"{route} create requires a non-empty {source_kind}", 400, "BAD_REQUEST", "SDK")
    lifecycle_raw = config.get("lifecycle_policy")
    if lifecycle_raw is not None and not isinstance(lifecycle_raw, dict):
        raise create_error("lifecycle_policy must be an object", 400, "BAD_REQUEST", "SDK")
    lifecycle = lifecycle_body(lifecycle_raw, creating=True) if lifecycle_raw is not None else None
    assert_lifecycle_class_support(
        lifecycle,
        sandbox_class=route,
        persistent=route == "macos" and config.get("persistent") is True,
    )
    # A mistyped value is refused, never silently dropped.
    for key, kind, description in (
        ("name", str, "a string"),
        ("spot", bool, "a boolean"),
        ("persistent", bool, "a boolean"),
        ("user", str, "a string"),
        ("cpu_arch", str, "a string"),
        ("cpu_type", str, "a string"),
        ("reservation", str, "a string"),
        ("auto_renew", bool, "a boolean"),
    ):
        _optional_typed(config, key, kind, description)
    # The platform's architectures (the contract's CpuArchitecture), as for the
    # image option: SDK inputs are closed.
    if config.get("cpu_arch") is not None and config.get("cpu_arch") not in ("amd64", "arm64"):
        raise create_error('cpu_arch must be "amd64" or "arm64"', 400, "BAD_REQUEST", "SDK")
    body: CreateBody = {}
    if not source_omitted:
        body[source_kind] = source
    name = config.get("name")
    if isinstance(name, str):
        body["name"] = name
    if source_kind == "image":
        persistent = config.get("persistent")
        if route == "macos" and isinstance(persistent, bool):
            body["persistent"] = persistent
        spot = config.get("spot")
        if isinstance(spot, bool):
            body["spot"] = spot
    if route == "linux" and source_kind == "image":
        cpu_arch = config.get("cpu_arch")
        if isinstance(cpu_arch, str):
            body["cpu_arch"] = cpu_arch
        cpu_type = config.get("cpu_type")
        if isinstance(cpu_type, str):
            body["cpu_type"] = cpu_type
    env = _string_map(config.get("env"))
    if env and source_kind == "image":
        body["env"] = env
    target = region_selector(config.get("target"))
    if target is not None:
        body["region_id"] = target
    if resources is not None and route == "linux" and source_kind == "image":
        if not isinstance(resources, Resources):
            raise create_error("resources must be Resources(...)", 400, "BAD_REQUEST", "SDK")
        for cores, wire, label in (
            (resources.cpu, "cpu", "cpu"),
            (resources.cpu_max, "cores_max", "cpu_max"),
        ):
            if cores is None:
                continue
            # A double on the wire: any finite positive number a double carries.
            if not finite_number(cores) or cores <= 0:
                raise create_error(
                    f"resources.{label} must be a positive number of cores", 400, "BAD_REQUEST", "SDK"
                )
            # The contract's cores_max is a positive multiple of 0.5.
            if wire == "cores_max" and not float(cores * 2).is_integer():
                raise create_error(
                    "resources.cpu_max must be a positive multiple of 0.5 cores", 400, "BAD_REQUEST", "SDK"
                )
            body[wire] = cores
        for gib, wire, label in (
            (resources.memory, "memory_mib", "resources.memory"),
            (resources.memory_max, "memory_max_mib", "resources.memory_max"),
            (resources.disk, "disk_mib", "resources.disk"),
            (resources.disk_max, "disk_max_mib", "resources.disk_max"),
        ):
            if gib is not None:
                mib = gib_to_mib(gib, label=label)
                # The contract types these sizes as int32 MiB.
                if mib > INT32_MAX:
                    raise create_error(
                        f"{label} must convert to at most 2147483647 MiB", 400, "BAD_REQUEST", "SDK"
                    )
                body[wire] = mib
    user = config.get("user")
    if isinstance(user, str) and route == "linux" and source_kind == "image":
        body["user"] = user
    ports = _port_requests(config.get("ports"))
    if ports is not None:
        body["ports"] = [{"port": port.port, "proto": port.proto} for port in ports]
    volumes = _volume_mounts(config.get("volumes"))
    if route in ("linux", "macos") and source_kind == "image" and volumes is not None:
        body["volumes"] = [_volume_mount(volume) for volume in volumes]
    secrets = _secret_names(config.get("secrets"))
    if secrets is not None and source_kind == "image":
        body["secrets"] = list(secrets)
    labels = _labels_with_name(config)
    if labels:
        body["labels"] = labels
    network_policy = config.get("network_policy")
    if route in ("linux", "macos") and source_kind == "image" and "network_policy" in config:
        body["network_policy"] = (
            None if network_policy is None else serialize_network_policy_input(network_policy)
        )
    if route == "macos":
        reservation = config.get("reservation")
        if isinstance(reservation, str):
            body["reservation"] = reservation
        auto_renew = config.get("auto_renew")
        if isinstance(auto_renew, bool):
            body["auto_renew"] = auto_renew
        for axis in ("cores", "memory_mib", "disk_mib"):
            amount = config.get(axis)
            if isinstance(amount, int) and not isinstance(amount, bool):
                body[axis] = amount
    if lifecycle:
        body["lifecycle_policy"] = lifecycle
    if "wait_seconds" in config:
        # Validate whatever the caller passed — None, a float or a bool is a
        # caller bug, not "use the default" (parity with the TS SDK's createWaitSeconds).
        wait_seconds = config["wait_seconds"]
        _validate_wait(wait_seconds)
        assert isinstance(wait_seconds, int)  # _validate_wait proved it
        # Above the timer maximum is no limit: the body carries the maximum, in
        # whole seconds, the same value in both SDKs (never a 400-digit integer).
        body["wait_seconds"] = min(wait_seconds, MAX_WIRE_WAIT_SECONDS)
    create_timeout_seconds = config.get("create_timeout_seconds")
    if create_timeout_seconds is not None:
        # The server's own budget (an int64 on the wire) that both SDKs carry exactly.
        if (
            isinstance(create_timeout_seconds, bool)
            or not isinstance(create_timeout_seconds, int)
            or not -1 <= create_timeout_seconds <= MAX_SAFE_INTEGER
        ):
            raise create_error(
                "create_timeout_seconds must be an integer from -1 (the server maximum) to 9007199254740991",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        body["create_timeout_seconds"] = create_timeout_seconds
    return body


def _create_path(config: CreateConfig) -> str:
    match (_create_route(config), _create_source_kind(config)):
        case ("linux", "image"):
            return "/sandboxes"
        case ("linux", "snapshot"):
            return "/sandboxes/from-snapshot"
        case ("macos", "image"):
            return "/sandboxes/macos"
        case _ as unreachable:
            raise AssertionError(f"unexpected route: {unreachable}")


def _create_route(config: CreateConfig) -> Literal["linux", "macos"]:
    route = config.get("route")
    if route in ("linux", "macos"):
        return route
    raise AssertionError(f"unexpected route: {route}")


def _create_source_kind(config: CreateConfig) -> Literal["image", "snapshot"]:
    source_kind = config.get("source_kind")
    if source_kind in ("image", "snapshot"):
        return source_kind
    raise AssertionError(f"unexpected source kind: {source_kind}")


def _create_response_state(
    payload: JsonObject,
) -> Literal[
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
]:
    return sandbox_state(str(payload.get("status") or ""))


def _create_wait_budget(config: CreateConfig) -> int:
    """The caller's create wait, uncapped (the body carries it capped)."""
    wait_seconds = config.get("wait_seconds")
    return wait_seconds if isinstance(wait_seconds, int) and not isinstance(wait_seconds, bool) else 60


def _validate_wait(wait_seconds: object) -> None:
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, int) or wait_seconds < 0:
        raise create_error("wait_seconds must be a non-negative integer", 400, "BAD_REQUEST", "SDK")


def _require_image_source(image: str | Image | ImageBuilder, method: str = "create") -> str:
    if isinstance(image, str):
        cleaned = image.strip()
        if cleaned:
            return cleaned
        raise create_error("image must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    expected = f"{method}() accepts an image name or img_… id string"
    if isinstance(image, ImageBuilder):
        hint = (
            "macOS sandboxes boot registered macOS images and do not build one"
            if method == "create_macos"
            else "use create_from_build(...) for an ImageBuilder"
        )
        raise create_error(f"{expected}; {hint}", 400, "BAD_REQUEST", "SDK")
    if isinstance(image, Image):
        raise create_error(f"{expected}; pass image.id", 400, "BAD_REQUEST", "SDK")
    raise create_error(expected, 400, "BAD_REQUEST", "SDK")


def _require_snapshot_source(snapshot: str) -> str:
    if not isinstance(snapshot, str):
        raise create_error(
            "create_from_snapshot() accepts a snapshot name or snap_… id string", 400, "BAD_REQUEST", "SDK"
        )
    cleaned = snapshot.strip()
    if not cleaned:
        raise create_error("snapshot must be a non-empty string", 400, "BAD_REQUEST", "SDK")
    return cleaned


def _source_image_ref(source: str) -> Optional[ImageRef]:
    """The caller's image as a reference when it is an ``img_`` id; a name is never an id."""
    return ImageRef(id=source, name=None) if source.startswith("img_") else None


def _source_snapshot_ref(source: str) -> Optional[SnapshotRef]:
    """The caller's snapshot as a reference when it is a ``snap_`` id; a name is never an id."""
    return SnapshotRef(id=source, name=None) if source.startswith("snap_") else None


def _volume_mount(mount: VolumeMount) -> JsonObject:
    body: JsonObject = {"volume": mount.volume_id, "mount_path": mount.mount_path}
    if mount.subpath:
        body["subpath"] = mount.subpath
    if mount.read_only:
        body["read_only"] = True
    return body


def _bad_create_value(message: str) -> DaytonaError:
    return create_error(message, 400, "BAD_REQUEST", "SDK")


def _list_items(
    value: CreateConfigValue | None, ok: Callable[[object], bool], message: str
) -> Optional[list[object]]:
    """A list/tuple create argument whose every entry passes ``ok``; ``None`` when absent.

    Anything else (a bare string, a mapping, a mistyped entry) is refused, never dropped.
    """
    if value is None:
        return None
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not all(ok(item) for item in value)
    ):
        raise _bad_create_value(message)
    return list(value)


def _is_volume_mount(item: object) -> bool:
    return (
        isinstance(item, VolumeMount)
        and isinstance(item.volume_id, str)
        and bool(item.volume_id.strip())
        and isinstance(item.mount_path, str)
        and bool(item.mount_path.strip())
        and (item.subpath is None or isinstance(item.subpath, str))
        and (item.read_only is None or isinstance(item.read_only, bool))
    )


def _is_port_mapping(item: object) -> bool:
    return (
        isinstance(item, PortMapping)
        and isinstance(item.port, int)
        and not isinstance(item.port, bool)
        and 1 <= item.port <= 65535
        # The contract's PortProtocol values only: SDK inputs are closed.
        and item.proto in ("tcp", "udp")
    )


def _volume_mounts(value: CreateConfigValue | None) -> Sequence[VolumeMount] | None:
    items = _list_items(
        value, _is_volume_mount, "volumes must be a list of VolumeMount(volume_id=..., mount_path=...)"
    )
    return None if items is None else [item for item in items if isinstance(item, VolumeMount)]


def _port_requests(value: CreateConfigValue | None) -> Sequence[PortMapping] | None:
    items = _list_items(
        value, _is_port_mapping, "ports must be a list of PortMapping(port=...) with a port in 1..65535"
    )
    return None if items is None else [item for item in items if isinstance(item, PortMapping)]


def _secret_names(value: CreateConfigValue | None) -> Sequence[str] | None:
    items = _list_items(
        value, lambda item: isinstance(item, str) and bool(item), "secrets must be a list of secret names"
    )
    return None if items is None else [item for item in items if isinstance(item, str)]


def _optional_typed(config: CreateConfig, key: str, kind: type, description: str) -> None:
    value = config.get(key)
    if value is not None and (not isinstance(value, kind) or (kind is not bool and isinstance(value, bool))):
        raise _bad_create_value(f"{key} must be {description}")


def _string_map(value: CreateConfigValue | None) -> dict[str, str] | None:
    if isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)}
    return None
