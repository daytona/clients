# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence as SequenceABC
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

import anyio

from . import _clock
from ._api_core import (
    expect_create_image_resp,
    expect_image,
    expect_images_page,
    expect_list_secrets_resp,
    expect_list_snapshots_resp,
    expect_list_volumes_resp,
    expect_mac_catalog,
    expect_mac_models,
    expect_secret,
    expect_snapshot,
    expect_volume,
    mac_resources_from_wire,
    secret_from_wire,
)
from ._context_upload import BuildContext, upload_build_context
from ._core import (
    INT32_MAX,
    RequestSpec,
    TransportPayload,
    _expect_model_from_dict,
    checked_seconds,
    enum_str,
    header_text,
    image_options_body,
    json_object_from_mapping,
    optional_query_text,
    page_params,
    parse_snapshot,
    path_segment,
    region_selector,
    wait_budget_seconds,
)
from ._gen.api import MacReservation as MacReservationWire
from ._gen.api import MacReservationBatch as MacReservationBatchWire
from ._gen.api import ReservationPage
from ._gen.api import Volume as VolumeWire
from ._gen.api import VolumeType as VolumeTypeWire
from ._image_context import prepared_context
from ._image_pull import ImagePull
from ._transport import AsyncTransport
from .build_logs import BuildLogEventCallback, BuildLogFollowResult, BuildLogsCallback, follow_build_logs
from .errors import ClientTimeoutError, DaytonaError, NotFoundError, create_error
from .image import ImageBuilder
from .types import (
    CpuArch,
    CreateReservationRequest,
    CreateReservationsBulkRequest,
    Image,
    JsonObject,
    JsonValue,
    ListResult,
    MacCatalog,
    MacModel,
    MacReservation,
    MacReservationBatch,
    MacReservationEndReason,
    MacReservationOrigin,
    MacReservationStatus,
    Secret,
    Snapshot,
    UpdateReservationRequest,
    Volume,
    VolumeState,
    VolumeType,
)

_T = TypeVar("_T")


_path = path_segment

# The types a create may name, from the contract.
_VOLUME_CREATE_TYPES: Tuple[str, ...] = tuple(
    str(member.value) for member in VolumeTypeWire if member.name != "UNKNOWN_DEFAULT_OPEN_API"
)


def _additional_cursor_from_mapping(payload: Mapping[str, JsonValue]) -> Optional[str]:
    next_cursor = payload.get("next_cursor")
    if isinstance(next_cursor, str):
        return next_cursor
    camel_case_cursor = payload.get("nextCursor")
    return camel_case_cursor if isinstance(camel_case_cursor, str) else None


async def _iter_pages(
    fetch_page: Callable[[Optional[str]], Awaitable[ListResult[_T]]], cursor: Optional[str] = None
) -> AsyncIterator[_T]:
    seen_cursors: set[str] = set()
    if cursor is not None:
        seen_cursors.add(cursor)
    current_cursor = cursor
    while True:
        page = await fetch_page(current_cursor)
        for item in page.items:
            yield item
        next_cursor = page.next_cursor
        # An empty cursor ends the list too, as in TypeScript.
        if not next_cursor or next_cursor == current_cursor or next_cursor in seen_cursors:
            return
        seen_cursors.add(next_cursor)
        current_cursor = next_cursor


def _check_context_image_options(name: str | None, idle_expiry_seconds: int | None) -> None:
    if name is not None and idle_expiry_seconds is not None:
        raise create_error(
            "idle_expiry_seconds applies only to an anonymous image; omit name or idle_expiry_seconds",
            400,
            "BAD_REQUEST",
            "SDK",
        )


class AsyncImagesService:
    """Image build/pull/list namespace."""

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def follow_build_logs(
        self,
        image_id: str,
        /,
        *,
        on_logs: BuildLogsCallback,
        on_event: Optional[BuildLogEventCallback] = None,
        cursor: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> BuildLogFollowResult:
        """Observe a submitted image build. ``timeout`` is the observation budget
        in seconds (not an execution TTL); omitted, ``0`` or a value above about
        24.8 days adds no deadline."""
        return await follow_build_logs(
            self._transport,
            f"/images/{_path(image_id, 'image_id')}/build-logs-url",
            on_logs=on_logs,
            on_event=on_event,
            cursor=cursor,
            timeout=timeout,
        )

    async def build(
        self,
        builder: ImageBuilder,
        /,
        *,
        name: Optional[str] = None,
        idle_expiry_seconds: Optional[int] = None,
        region: Optional[str] = None,
        cpu_arch: Optional[CpuArch] = None,
    ) -> Image:
        """Build (or import) an image from an :class:`ImageBuilder` declaration.

        The builder is the only way to say WHAT to build — ``ImageBuilder.
        from_dockerfile(...)``, ``.from_registry(...)``, or the fluent
        ``.run_commands`` / ``.env`` / ``.workdir`` chain — in both SDKs
        (parity with TypeScript's ``images.build(builder, options)``). ``name``
        omitted makes the image anonymous; ``idle_expiry_seconds`` is then its
        idle-retention window — deleted once that long has passed without a
        sandbox being created from it (every create restarts the clock);
        omitted ⇒ the maximum retention supported by the API. A ``name`` may
        not start with a resource id prefix (``img_``, ``snap_``, ``sbx_``,
        ``vol_``, ``fn_``; :class:`ImageNameReservedError`). A name that already
        exists for different content or import options raises
        :class:`ImageNameTakenError` and changes nothing; an identical named
        import returns the existing image (a failed one is imported again).
        ``region``
        follows create's rule: omitted, blank or ``local`` (any case, any
        surrounding whitespace) uses the project's default region and sends no
        ``region_id``; any other value is sent as given and must be printable
        ASCII with no leading or trailing whitespace. The submission is an
        ordinary request, bounded by the client's request timeout; it returns
        without waiting for the build (``get(image.id, wait="ready")``).
        Local files recorded by the builder are read at build time, archived
        deterministically and uploaded as a build context before submission.
        Each upload part fails once no bytes move for the client's request
        timeout (``0``: never); each completion attempt uses the greater of
        that timeout and 600 seconds (``0``: no bound). No total deadline
        bounds the upload.
        With local files, ``name`` and ``idle_expiry_seconds`` cannot be
        combined: idle expiry applies only to anonymous images.
        """
        if not isinstance(builder, ImageBuilder):
            raise create_error(
                "images.build() takes an ImageBuilder (ImageBuilder.from_dockerfile(...), "
                ".from_registry(...)); it does not accept a raw dockerfile string",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        resolved_builder = builder
        body = image_options_body(
            name=name, idle_expiry_seconds=idle_expiry_seconds, region=region, cpu_arch=cpu_arch
        )
        if resolved_builder._local_sources:
            # Validate the upload-only restriction before reading local files.
            _check_context_image_options(name, idle_expiry_seconds)
            async with prepared_context(resolved_builder, "Dockerfile") as context:
                return await self.build_from_context(
                    context,
                    name=name,
                    idle_expiry_seconds=idle_expiry_seconds,
                    region=region,
                    cpu_arch=cpu_arch,
                )
        source, rendered_dockerfile = resolved_builder._request_source()
        if source is not None:
            body["source"] = source
        if rendered_dockerfile is not None:
            body["dockerfile"] = rendered_dockerfile
        created = expect_create_image_resp(
            await self._transport.send(RequestSpec("POST", "/images", json_body=body))
        )
        return await self.get(created.id)

    async def build_from_context(
        self,
        context: BuildContext,
        /,
        *,
        dockerfile: str = "Dockerfile",
        name: Optional[str] = None,
        idle_expiry_seconds: Optional[int] = None,
        region: Optional[str] = None,
        cpu_arch: Optional[CpuArch] = None,
        timeout: Optional[float] = None,
    ) -> Image:
        """Build an image from a directory or a build-context tar archive.

        ``context`` is a directory path, archive bytes or the path of a ``.tar``
        file, up to 1 GiB; ``dockerfile`` is the Dockerfile's path inside it.
        Directories are archived deterministically with BuildKit ignore rules:
        ``<dockerfile>.dockerignore`` takes precedence over ``.dockerignore``.
        The Dockerfile and the ignore files are always included. The archive
        is uploaded straight to storage in parts and verified by the API
        before the build starts; an identical archive the project already
        uploaded is reused without uploading it again. The other arguments
        are those of :meth:`build`. ``name`` and ``idle_expiry_seconds``
        cannot be combined: idle expiry applies only to anonymous images.
        ``timeout`` bounds the build request that follows the upload, in
        seconds, replacing the client's request timeout for it; ``0``, or a
        value above about 24.8 days, is no limit. The upload keeps its own
        bounds. Returns as soon as the build is submitted; wait with
        :meth:`get` (``wait="ready"``).
        """
        # Options first, then the pair, then the timeout (TypeScript checks in
        # the same order), all before the upload.
        body = image_options_body(
            name=name, idle_expiry_seconds=idle_expiry_seconds, region=region, cpu_arch=cpu_arch
        )
        # The API refuses the pair only at the build.
        _check_context_image_options(name, idle_expiry_seconds)
        request_timeout = None if timeout is None else checked_seconds(timeout)
        # images.build() archives builders; here a context is bytes or a path.
        if isinstance(context, ImageBuilder):
            raise create_error(
                "build_from_context() takes the bytes or path of a build context; build an ImageBuilder with images.build()",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        async with prepared_context(context, dockerfile) as archive:
            upload_id, upload_region = await upload_build_context(
                self._transport, archive, dockerfile=dockerfile, region=region_selector(region)
            )
        # The build must name the region the upload was verified in.
        body["context_upload_id"] = upload_id
        body["region_id"] = upload_region
        created = expect_create_image_resp(
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/images",
                    json_body=body,
                    timeout=request_timeout,
                )
            )
        )
        return await self.get(created.id)

    async def pull(
        self,
        image: str,
        /,
        *,
        name: Optional[str] = None,
        idle_expiry_seconds: Optional[int] = None,
        region: Optional[str] = None,
        cpu_arch: Optional[CpuArch] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Image:
        """Import a registry image, waiting until ready unless ``wait=False``.

        Takes the same image options as :meth:`build`. ``timeout`` is the
        total pull budget in seconds (submission and readiness); omitted,
        zero or a value above about 24.8 days waits without a deadline. The submission is also bounded by the
        client's request timeout. A failed import raises
        :class:`ImageBuildFailedError` with the server's recorded error.
        """
        if not isinstance(wait, bool):
            raise create_error("wait must be a boolean", 400, "BAD_REQUEST", "SDK")
        source, _ = ImageBuilder.from_registry(image)._request_source()
        body: JsonObject = {
            "source": source,
            **image_options_body(
                name=name, idle_expiry_seconds=idle_expiry_seconds, region=region, cpu_arch=cpu_arch
            ),
        }
        budget = None if timeout is None else wait_budget_seconds(timeout) or None
        return await ImagePull(self._transport, budget).run(
            RequestSpec("POST", "/images", json_body=body), wait=wait
        )

    async def get(
        self,
        image_id: str,
        /,
        *,
        wait: Literal["ready"] | None = None,
        timeout: Optional[float] = None,
    ) -> Image:
        """Fetch an image; ``wait="ready"`` waits for it to finish building.

        With a positive ``timeout`` (seconds) the wait repeats the server's
        long poll (each at most 60 s) until the image is ``ready``, ``failed``
        or ``error`` and returns it (check its status); a budget above the
        timer maximum waits without a deadline. A budget that runs out first
        raises :class:`RequestTimeoutError`. Without one (omitted or ``0``), a
        single long poll of the server's default length returns what the
        server reports.
        """
        return await _get_artifact(
            self._transport,
            f"/images/{_path(image_id, 'image_id')}",
            wait,
            timeout,
            expect_image,
            lambda image: image.status,
        )

    async def promote(self, image_id: str, name: str, /) -> Image:
        """Name an ANONYMOUS image, making it permanent (``PATCH /images/{id}``).

        Promotion only: an image that already has a name answers 409 — renaming
        a named image is not offered (sandboxes reference images by id, so a
        rename would silently relabel every sandbox created from it). A name
        that already exists raises :class:`ImageNameTakenError`; a reserved
        prefix (``img_``, ``snap_``, ``sbx_``, ``vol_``, ``fn_``) raises
        :class:`ImageNameReservedError`.
        """
        path = f"/images/{_path(image_id, 'image_id')}"
        if not isinstance(name, str) or not name.strip():
            raise create_error("name must be a non-empty string", 400, "BAD_REQUEST", "SDK")
        await self._transport.send(RequestSpec("PATCH", path, json_body={"name": name}))
        return await self.get(image_id)

    async def delete(self, image_id: str, /) -> None:
        await self._transport.send(RequestSpec("DELETE", f"/images/{_path(image_id, 'image_id')}"))

    async def list(
        self,
        *,
        anonymous: bool = False,
        sandbox_id: Optional[str] = None,
        name: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        platform: Optional[Literal["true", "false", "any"]] = None,
    ) -> ListResult[Image]:
        """List images (``GET /images``).

        ``platform`` picks the shared images Daytona publishes for every
        project: ``"false"`` (the server default) lists the project's own
        images, ``"true"`` lists only the non-deprecated platform images (the
        default one carries ``default=True``), and ``"any"`` lists both.
        """
        params = page_params(cursor, limit)
        if not isinstance(anonymous, bool):
            raise create_error("anonymous must be a boolean", 400, "BAD_REQUEST", "SDK")
        params["anonymous"] = anonymous
        if platform is not None:
            # The contract's values only: SDK inputs are closed.
            if platform not in ("true", "false", "any"):
                raise create_error('platform must be "true", "false" or "any"', 400, "BAD_REQUEST", "SDK")
            params["platform"] = platform
        if optional_query_text(sandbox_id, "sandbox_id") is not None:
            params["sandbox_id"] = sandbox_id
        if optional_query_text(name, "name") is not None:
            params["name"] = name
        response = await self._transport.send(RequestSpec("GET", "/images", params=params))
        return expect_images_page(response)

    async def iter(
        self,
        *,
        anonymous: bool = False,
        sandbox_id: Optional[str] = None,
        name: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        platform: Optional[Literal["true", "false", "any"]] = None,
    ) -> AsyncIterator[Image]:
        async for item in _iter_pages(
            lambda next_cursor: self.list(
                anonymous=anonymous,
                sandbox_id=sandbox_id,
                name=name,
                cursor=next_cursor,
                limit=limit,
                platform=platform,
            ),
            cursor,
        ):
            yield item


_ARTIFACT_LONGPOLL_MAX_MS = 60_000
#: A long poll that answers sooner than this without a settled status is
#: followed by a pause, so a server that does not hold is not polled in a loop.
_ARTIFACT_MIN_POLL_INTERVAL_S = 1.0
_ARTIFACT_SETTLED_STATUSES = frozenset({"ready", "failed", "error"})
_ARTIFACT_HOLD_READ_SLACK_S = 5.0

_Artifact = TypeVar("_Artifact")


async def _get_artifact(
    transport: AsyncTransport,
    path: str,
    wait: Optional[Literal["ready"]],
    timeout: Optional[float],
    parse: Callable[[TransportPayload], _Artifact],
    status_of: Callable[[_Artifact], str],
) -> _Artifact:
    """Read an artifact, as one hold or as repeated holds within a wait budget.

    Without a budget (``timeout`` omitted or ``0``) ``wait="ready"`` makes one
    hold of the server's default length and returns what the server reports. A
    positive budget repeats holds of at most 60 s until the artifact settles
    (``ready``, ``failed`` or ``error``), which is returned for the caller to
    check; a budget above the timer maximum repeats them without a deadline.
    Every read is capped at the wait's deadline, as the sandbox waits are, and
    a budget that runs out before the artifact settles raises
    :class:`RequestTimeoutError` (408, ``REQUEST_TIMEOUT``, ``SDK``), as
    ``images.pull()`` and ``sandbox.wait()`` do: never an unsettled observation.
    """
    if wait is not None and wait != "ready":
        raise create_error(f'wait must be "ready" or omitted, got {wait!r}', 400, "BAD_REQUEST", "SDK")
    # The timeout is checked with or without ``wait``, as in TypeScript; without
    # ``wait`` a valid one is unused. Above the timer maximum (an integer too
    # large for a float included) the budget maps to 0.0: holds repeat without
    # a deadline.
    budget = None if timeout is None else wait_budget_seconds(timeout)
    if wait is None:
        return parse(await transport.send(RequestSpec("GET", path)))
    if budget is None or (not isinstance(timeout, bool) and timeout == 0):
        return parse(
            await transport.send(RequestSpec("GET", path, params={"wait": wait}, unbounded_read=True))
        )
    deadline = None if budget == 0 else _clock.monotonic() + budget

    def timed_out() -> DaytonaError:
        return create_error("artifact did not become ready before timeout", 408, "REQUEST_TIMEOUT", "SDK")

    while True:
        remaining = None if deadline is None else deadline - _clock.monotonic()
        if remaining is not None and remaining <= 0:
            raise timed_out()
        # A long poll is governed by the wait budget, not the request timeout:
        # each hold is the smaller of the remaining budget and the server's
        # 60 s cap, in whole milliseconds, and the read adds network slack —
        # never past the wait's deadline, as the sandbox waits do.
        hold_ms = (
            _ARTIFACT_LONGPOLL_MAX_MS
            if remaining is None
            else max(1, min(_ARTIFACT_LONGPOLL_MAX_MS, math.ceil(remaining * 1000)))
        )
        slack_read = hold_ms / 1000 + _ARTIFACT_HOLD_READ_SLACK_S
        started = _clock.monotonic()
        try:
            artifact = parse(
                await transport.send(
                    RequestSpec(
                        "GET",
                        path,
                        params={"wait": wait, "timeout_ms": hold_ms},
                        timeout=slack_read if remaining is None else min(slack_read, remaining),
                    )
                )
            )
        except ClientTimeoutError as error:
            # Only expiry of this wait's own cap is the wait running out.
            if (
                deadline is not None
                and remaining is not None
                and (remaining <= slack_read or _clock.monotonic() >= deadline)
            ):
                raise timed_out() from error
            raise
        if status_of(artifact) in _ARTIFACT_SETTLED_STATUSES:
            return artifact
        if deadline is not None and _clock.monotonic() >= deadline:
            raise timed_out()
        elapsed = _clock.monotonic() - started
        if elapsed < _ARTIFACT_MIN_POLL_INTERVAL_S:
            pause = _ARTIFACT_MIN_POLL_INTERVAL_S - elapsed
            if deadline is not None:
                pause = min(pause, max(0.0, deadline - _clock.monotonic()))
            await anyio.sleep(pause)


class AsyncSnapshotsService:
    """Sandbox snapshot namespace."""

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def list(
        self,
        *,
        name: Optional[str] = None,
        sandbox_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ListResult[Snapshot]:
        params = page_params(cursor, limit)
        if optional_query_text(name, "name") is not None:
            params["name"] = name
        if optional_query_text(sandbox_id, "sandbox_id") is not None:
            params["sandbox_id"] = sandbox_id
        response = await self._transport.send(RequestSpec("GET", "/snapshots", params=params))
        page = expect_list_snapshots_resp(response)
        return ListResult(
            items=[parse_snapshot(item) for item in page.snapshots], next_cursor=page.next_cursor
        )

    async def iter(
        self,
        *,
        name: Optional[str] = None,
        sandbox_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[Snapshot]:
        async for item in _iter_pages(
            lambda next_cursor: self.list(
                name=name,
                sandbox_id=sandbox_id,
                cursor=next_cursor,
                limit=limit,
            ),
            cursor,
        ):
            yield item

    async def get(
        self,
        snapshot_id: str,
        /,
        *,
        wait: Optional[Literal["ready"]] = None,
        timeout: Optional[float] = None,
    ) -> Snapshot:
        """Fetch a snapshot; ``wait="ready"`` waits as :meth:`AsyncImagesService.get` does."""
        return await _get_artifact(
            self._transport,
            f"/snapshots/{_path(snapshot_id, 'snapshot_id')}",
            wait,
            timeout,
            lambda payload: parse_snapshot(expect_snapshot(payload)),
            lambda snapshot: snapshot.status,
        )

    async def delete(self, snapshot_id: str, /) -> None:
        """Delete a snapshot (409 while sandboxes still depend on it)."""
        await self._transport.send(RequestSpec("DELETE", f"/snapshots/{_path(snapshot_id, 'snapshot_id')}"))


class AsyncVolumesService:
    """Volume management namespace."""

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def list(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ListResult[Volume]:
        """List volumes."""
        return await self._list_page(cursor=cursor, limit=limit)

    async def _list_page(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ListResult[Volume]:
        params: Dict[str, JsonValue] = page_params(cursor, limit)
        response = await self._transport.send(RequestSpec("GET", "/volumes", params=params))
        page = expect_list_volumes_resp(response)
        return ListResult(items=[_volume(volume) for volume in page.volumes], next_cursor=page.next_cursor)

    async def iter(
        self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[Volume]:
        async for item in _iter_pages(
            lambda next_cursor: self._list_page(
                cursor=next_cursor,
                limit=limit,
            ),
            cursor,
        ):
            yield item

    async def get(self, name_or_id: str, /, *, create: bool = False, id_only: bool = False) -> Volume:
        """Fetch by id or name; ``id_only`` disables name fallback."""
        path = f"/volumes/{_path(name_or_id, 'name_or_id')}"
        # A truthy string must never act as True (it would create a volume).
        for flag, value in (("create", create), ("id_only", id_only)):
            if not isinstance(value, bool):
                raise create_error(f"{flag} must be a boolean", 400, "BAD_REQUEST", "SDK")
        params: Dict[str, JsonValue] = {"id_only": True} if id_only else {}
        try:
            return _volume(expect_volume(await self._transport.send(RequestSpec("GET", path, params=params))))
        except NotFoundError:
            if not create or id_only:
                raise
            return await self.create(name=name_or_id)

    async def create(
        self,
        *,
        name: str,
        size_gib: Optional[int] = None,
        type: Optional[Literal["disk", "bucket"]] = None,
    ) -> Volume:
        """Create a volume.

        ``type`` omitted lets the server choose the volume's type; ``size_gib``
        (disk volumes only) omitted uses the server default.
        """
        if not isinstance(name, str) or not name.strip():
            raise create_error("name must be a non-empty string", 400, "BAD_REQUEST", "SDK")
        if type is not None and type not in _VOLUME_CREATE_TYPES:
            raise create_error(
                f"type must be one of {', '.join(_VOLUME_CREATE_TYPES)}", 400, "BAD_REQUEST", "SDK"
            )
        if size_gib is not None and (
            isinstance(size_gib, bool)
            or not isinstance(size_gib, int)
            or size_gib <= 0
            or size_gib > INT32_MAX
        ):
            # The contract's int32.
            raise create_error(
                f"size_gib must be a positive integer number of GiB up to {INT32_MAX}",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        body: JsonObject = {"name": name}
        if type is not None:
            body["type"] = type
        if size_gib is not None:
            body["size_gib"] = size_gib
        created = _volume(
            expect_volume(await self._transport.send(RequestSpec("POST", "/volumes", json_body=body)))
        )
        if not created.id.strip():
            # A volume without a usable id cannot be mounted or deleted.
            raise create_error("volume response is missing its id", 400, "BAD_REQUEST", "SDK")
        return created

    async def delete(self, name_or_id: str, /, *, id_only: bool = False) -> None:
        """Request deletion by id or name and return once the API accepts it; ``id_only`` disables name lookup."""
        path = f"/volumes/{_path(name_or_id, 'name_or_id')}"
        if not isinstance(id_only, bool):
            raise create_error("id_only must be a boolean", 400, "BAD_REQUEST", "SDK")
        params: Dict[str, JsonValue] = {"id_only": True} if id_only else {}
        await self._transport.send(RequestSpec("DELETE", path, params=params))


class AsyncReservationsService:
    """Dedicated-Mac reservations, which macOS sandboxes run on.

    A macOS sandbox runs on a Mac the organization has reserved: reserve one
    here, or let a macOS create buy one (``reservation="new"``, or the
    organization's auto-reserve). A reservation lasts 24-hour periods, each
    charged when it starts. Reserving, renewing and turning auto-renew on need
    macOS access enabled for the organization (otherwise ``403``
    ``FEATURE_NOT_ENABLED``, raised as :class:`FeatureNotEnabledError`);
    listing, reading and turning auto-renew off do not.
    """

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def create(
        self,
        *,
        region: str,
        model: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        auto_renew: Optional[bool] = None,
    ) -> MacReservation:
        """Reserve a Mac of ``model`` in ``region``.

        ``model``: Mac model slug from GET /mac-models. Omitted uses the region's default model. An unavailable model is never upgraded.

        The request always carries an ``Idempotency-Key``: ``idempotency_key``
        when given, otherwise a random UUID the SDK generates for this call.
        Pass your own key if you may retry, so a retry after a lost response
        returns the same reservation instead of reserving a second Mac.
        ``auto_renew`` defaults server-side to the organization's auto-reserve
        setting. Raises :class:`ConflictError` with
        ``code == "NO_MAC_AVAILABLE"`` when no Mac of that model is free in the
        region, and :class:`LimitExceededError` (a :class:`RateLimitError`) with
        ``limit == "max_mac_reservations"`` when the organization holds its Mac reservation limit.
        """
        _check_reservation_options(region=region, model=model, auto_renew=auto_renew)
        headers = _idempotency_headers(idempotency_key)
        body: CreateReservationRequest = {"region_id": region}
        if model is not None:
            body["model"] = model
        if auto_renew is not None:
            body["auto_renew"] = auto_renew
        response = await self._transport.send(
            RequestSpec("POST", "/reservations", json_body=json_object_from_mapping(body), headers=headers)
        )
        return _mac_reservation(response)

    async def create_bulk(
        self,
        *,
        region: str,
        count: int,
        min_count: Optional[int] = None,
        model: Optional[str] = None,
        auto_renew: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> MacReservationBatch:
        """Reserve count Macs of one model in region in one request.

        min_count (default count) is the fewest to accept: with fewer Macs free or fewer left under the organization's Mac reservation limit, the request reserves as many as are free and fit, or none, failing with LimitExceededError (a RateLimitError) limit == "max_mac_reservations" when fewer than min_count fit under the limit (checked first), or with ConflictError code == "NO_MAC_AVAILABLE" when fewer than min_count are free. The request always carries an Idempotency-Key: idempotency_key when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response returns the same reservations instead of reserving more Macs.
        """
        _check_reservation_options(
            region=region, model=model, auto_renew=auto_renew, count=count, min_count=min_count
        )
        headers = _idempotency_headers(idempotency_key)
        body: CreateReservationsBulkRequest = {"region_id": region, "count": count}
        if min_count is not None:
            body["min_count"] = min_count
        if model is not None:
            body["model"] = model
        if auto_renew is not None:
            body["auto_renew"] = auto_renew
        response = await self._transport.send(
            RequestSpec(
                "POST", "/reservations/bulk", json_body=json_object_from_mapping(body), headers=headers
            )
        )
        batch = _expect_model_from_dict(
            response,
            MacReservationBatchWire,
            "bulk reservation response",
            MacReservationBatchWire.model_validate,
        )
        return MacReservationBatch(
            requested=batch.requested,
            reserved=batch.reserved,
            reservations=tuple(_mac_reservation(item) for item in batch.reservations),
        )

    async def list(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> ListResult[MacReservation]:
        """One page of the organization's reservations."""
        params: Dict[str, JsonValue] = page_params(cursor, limit)
        response = await self._transport.send(RequestSpec("GET", "/reservations", params=params))
        page = _expect_model_from_dict(
            response, ReservationPage, "reservation list response", ReservationPage.model_validate
        )
        return ListResult(
            items=[_mac_reservation(item) for item in page.reservations], next_cursor=page.next_cursor
        )

    async def iter(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[MacReservation]:
        """Every reservation of the organization, draining page by page."""
        async for item in _iter_pages(
            lambda next_cursor: self.list(cursor=next_cursor, limit=limit),
            cursor,
        ):
            yield item

    async def get(self, reservation_id: str, /) -> MacReservation:
        return _mac_reservation(
            await self._transport.send(
                RequestSpec("GET", f"/reservations/{_path(reservation_id, 'reservation_id')}")
            )
        )

    async def renew(self, reservation_id: str, /, *, idempotency_key: Optional[str] = None) -> MacReservation:
        """Renew the reservation for its next period, which the server charges.

        The request always carries an Idempotency-Key: idempotency_key when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response does not pay for a second period.
        Raises LimitExceededError (a RateLimitError) with limit == "max_mac_reservations" when the renewal would keep the organization over its Mac reservation limit.
        """
        # The id first, then the options, as TypeScript checks them.
        path = f"/reservations/{_path(reservation_id, 'reservation_id')}/renew"
        headers = _idempotency_headers(idempotency_key)
        return _mac_reservation(await self._transport.send(RequestSpec("POST", path, headers=headers)))

    async def update(self, reservation_id: str, /, *, auto_renew: bool) -> MacReservation:
        """Turn the reservation's auto-renewal on or off.

        Turning it on needs macOS access enabled for the organization;
        turning it off works without it, so an organization can stop a reservation
        renewing. Raises :class:`ConflictError` when the reservation is no longer live.
        """
        # The id first, then the options, as TypeScript checks them.
        path = f"/reservations/{_path(reservation_id, 'reservation_id')}"
        if not isinstance(auto_renew, bool):
            raise create_error("auto_renew must be a boolean", 400, "BAD_REQUEST", "SDK")
        body: UpdateReservationRequest = {"auto_renew": auto_renew}
        return _mac_reservation(
            await self._transport.send(
                RequestSpec(
                    "PATCH",
                    path,
                    json_body=json_object_from_mapping(body),
                )
            )
        )


def _check_reservation_options(
    *, region: object, model: object, auto_renew: object, count: object = 1, min_count: object = None
) -> None:
    """Every reservation option is checked before any request, so none is
    silently dropped from the body (as in TypeScript)."""

    def refuse(message: str) -> DaytonaError:
        return create_error(message, 400, "BAD_REQUEST", "SDK")

    # A blank region names no region and a blank model is the region's default
    # model (the contract), so only a non-string is refused.
    if not isinstance(region, str):
        raise refuse("region must be a string")
    if model is not None and not isinstance(model, str):
        raise refuse("model must be a string")
    if auto_renew is not None and not isinstance(auto_renew, bool):
        raise refuse("auto_renew must be a boolean")
    # Billed: the counts the contract allows only (count 1 to 100, min_count 1 to count).
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise refuse("count must be an integer from 1 to 100")
    if min_count is not None and (
        isinstance(min_count, bool) or not isinstance(min_count, int) or not 1 <= min_count <= count
    ):
        raise refuse("min_count must be an integer from 1 to count")


def _mac_reservation(response: TransportPayload | MacReservationWire) -> MacReservation:
    value = _expect_model_from_dict(
        response,
        MacReservationWire,
        "reservation response",
        MacReservationWire.model_validate,
    )
    raw_status = enum_str(value.status)
    raw_origin = enum_str(value.origin)
    raw_end_reason = enum_str(value.end_reason) if value.end_reason is not None else None
    return MacReservation(
        id=value.id,
        org_id=value.org_id,
        project_id=value.project_id,
        region=value.region_id,
        status=_mac_reservation_status(raw_status),
        raw_status=raw_status,
        period_n=value.period_n,
        period_seconds=value.period_seconds,
        created_at=value.created_at,
        slots_used=value.slots_used,
        renewal_pending=value.renewal_pending,
        auto_renew=value.auto_renew,
        origin=_mac_reservation_origin(raw_origin),
        raw_origin=raw_origin,
        resources_used=mac_resources_from_wire(value.resources_used),
        activated_at=value.activated_at,
        capacity=mac_resources_from_wire(value.capacity) if value.capacity is not None else None,
        model=value.model,
        end_reason=_mac_reservation_end_reason(raw_end_reason),
        raw_end_reason=raw_end_reason,
        ended_at=value.ended_at,
        pending_mac_started_at=value.pending_mac_started_at,
        period_end=value.period_end,
        period_started_at=value.period_started_at,
        reissued_at=value.reissued_at,
        renewal_error=value.renewal_error,
    )


def _volume(value: VolumeWire) -> Volume:
    raw_type = enum_str(value.type)
    raw_status = enum_str(value.status)
    return Volume(
        id=value.id,
        name=value.name,
        type=_volume_type(raw_type),
        raw_type=raw_type,
        status=_volume_state(raw_status),
        raw_status=raw_status,
        created_at=value.created_at,
        updated_at=value.updated_at,
        size_gib=value.size_gib,
        error=value.error,
        last_used_at=value.last_used_at,
        # A plain dict owned by this volume (read-only to type checkers), so
        # asdict / deepcopy / pickle / json.dumps work on the model.
        options=json_object_from_mapping(value.options) if value.options is not None else None,
        used_bytes=value.used_bytes,
        used_bytes_at=value.used_bytes_at,
    )


def _volume_state(value: str) -> VolumeState:
    if value in {"ready", "deleting", "deleted", "error"}:
        return value
    return "unknown"


def _volume_type(value: str) -> VolumeType:
    if value == "disk" or value == "bucket":
        return value
    return "unknown"


def _mac_reservation_status(value: str) -> MacReservationStatus:
    if value in {"pending_charge", "pending_mac", "active", "ending", "released", "failed"}:
        return value
    return "unknown"


def _mac_reservation_end_reason(value: str | None) -> MacReservationEndReason | None:
    if value is None:
        return None
    if value in {"expired", "released", "charge_failed", "not_accepted", "reissued", "operator"}:
        return value
    return "unknown"


def _mac_reservation_origin(value: str) -> MacReservationOrigin:
    if value == "explicit" or value == "auto":
        return value
    return "unknown"


def _idempotency_headers(idempotency_key: Optional[str]) -> Dict[str, str]:
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        raise create_error("idempotency_key must be a string", 400, "BAD_REQUEST", "SDK")
    if idempotency_key is None:
        return {"Idempotency-Key": str(uuid.uuid4())}
    return {"Idempotency-Key": header_text(idempotency_key, "idempotency_key")}


class AsyncMacModelsService:
    """The Mac model registry (``GET /mac-models``).

    Lists the models ``reservations.create(model=...)`` accepts, with each
    model's CPU cores, physical memory, physical SSD capacity and sandbox
    disk room in MiB, maximum sandbox count, and default sandbox size. ``catalog()`` adds free Macs
    and prices.
    """

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def list(self) -> Tuple[MacModel, ...]:
        """Every registered Mac model, ordered by ``id``."""
        return tuple(expect_mac_models(await self._transport.send(RequestSpec("GET", "/mac-models"))))

    async def catalog(self, *, region: Optional[str] = None) -> MacCatalog:
        """Free Macs and prices per Mac model (GET /mac-catalog). available is advisory; price is null when pricing is unavailable."""
        # One region string, checked as every query value is.
        selected = optional_query_text(region, "region")
        params: Dict[str, JsonValue] = {} if selected is None else {"region_ids": selected}
        return expect_mac_catalog(
            await self._transport.send(RequestSpec("GET", "/mac-catalog", params=params))
        )


class AsyncSecretsService:
    """Project secret namespace.

    Project-scoped secrets: named key/value pairs stored
    encrypted in the project. Attach one or more to a sandbox at creation via
    the ``secrets`` create parameter (a list of names). Attached secrets are
    available to outbound HTTPS requests to their allowed domains, not plain
    HTTP requests. Their values never enter the sandbox and are never returned
    by the API (write-only).
    """

    def __init__(self, _transport: AsyncTransport) -> None:
        self._transport = _transport

    async def list(self, *, cursor: Optional[str] = None, limit: Optional[int] = None) -> ListResult[Secret]:
        """List secret metadata."""
        params: Dict[str, JsonValue] = page_params(cursor, limit)
        response = await self._transport.send(RequestSpec("GET", "/secrets", params=params))
        payload = expect_list_secrets_resp(response)
        return ListResult(
            items=[secret_from_wire(item) for item in payload.secrets], next_cursor=payload.next_cursor
        )

    async def iter(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[Secret]:
        async for item in _iter_pages(lambda next_cursor: self.list(cursor=next_cursor, limit=limit), cursor):
            yield item

    async def get(self, name: str, /) -> Secret:
        """Fetch one secret's metadata (never its value)."""
        return expect_secret(
            await self._transport.send(RequestSpec("GET", f"/secrets/{_path(name, 'name')}"))
        )

    async def set(
        self,
        name: str,
        value: str,
        /,
        *,
        allowed_domains: Sequence[str],
        description: Optional[str] = None,
        if_not_exists: bool = False,
    ) -> Secret:
        """Create or update a write-only secret.

        ``value`` must not be empty. ``allowed_domains``: The hosts where this
        secret's value may be sent (exact or ``*.`` wildcard), or ``["*"]`` for
        any verified host. ``"*"`` lets the value reach any HTTPS host,
        including one a compromised sandbox controls. ``description`` omitted
        (or ``None``) keeps the stored description of an existing secret, read
        first with :meth:`get` (one more request, not atomic with the write; an
        error from it other than not-found fails the call; no read with
        ``if_not_exists=True``); ``""`` clears it.
        """
        path = f"/secrets/{_path(name, 'name')}"
        if not isinstance(value, str):
            raise create_error("value must be a string", 400, "BAD_REQUEST", "SDK")
        if not value:
            raise create_error("value must not be empty", 400, "BAD_REQUEST", "SDK")
        if description is not None and not isinstance(description, str):
            raise create_error("description must be a string", 400, "BAD_REQUEST", "SDK")
        if not isinstance(if_not_exists, bool):
            raise create_error("if_not_exists must be a boolean", 400, "BAD_REQUEST", "SDK")
        if (
            isinstance(allowed_domains, str)
            or not isinstance(allowed_domains, SequenceABC)
            or not all(isinstance(domain, str) for domain in allowed_domains)
        ):
            raise create_error("allowed_domains must be a list of strings", 400, "BAD_REQUEST", "SDK")
        if not allowed_domains:
            # The API rejects an empty list (`["*"]` is any verified host).
            raise create_error("allowed_domains must not be empty", 400, "BAD_REQUEST", "SDK")
        body: JsonObject = {"value": value, "allowed_domains": list(allowed_domains)}
        if description is None and not if_not_exists:
            # The API replaces the whole secret: keep the stored description.
            try:
                description = (await self.get(name)).description
            except NotFoundError:
                description = None
        if description is not None:
            body["description"] = description
        headers = {"If-None-Match": "*"} if if_not_exists else {}
        return expect_secret(
            await self._transport.send(RequestSpec("PUT", path, json_body=body, headers=headers))
        )

    async def delete(self, name: str, /) -> None:
        """Delete a secret."""
        await self._transport.send(RequestSpec("DELETE", f"/secrets/{_path(name, 'name')}"))
