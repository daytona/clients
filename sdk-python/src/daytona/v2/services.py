# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import uuid
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    TypeVar,
)
from urllib.parse import quote

from ._api_core import (
    expect_create_image_resp,
    expect_images_page,
    expect_list_secrets_resp,
    expect_list_snapshots_resp,
    expect_list_volumes_resp,
    expect_secret,
    expect_snapshot,
    expect_volume,
)
from ._core import (
    RequestSpec,
    TransportPayload,
    _expect_model_from_dict,
    enum_str,
    json_object_from_mapping,
    parse_image,
    parse_snapshot,
    timeout_millis_from_seconds,
)
from ._gen.api import MacReservation as MacReservationWire
from ._gen.api import MacReservationBatch as MacReservationBatchWire
from ._gen.api import Volume as VolumeWire
from ._image_pull import ImagePull
from ._transport import AsyncTransport
from .build_logs import BuildLogFollowResult, _EventCallback, _LogsCallback, follow_build_logs
from .errors import NotFoundError, create_error
from .image import ImageBuilder
from .types import (
    CreateReservationRequest,
    CreateReservationsBulkRequest,
    Image,
    JsonObject,
    JsonValue,
    ListResult,
    MacCatalog,
    MacModel,
    MacModelList,
    MacReservation,
    MacReservationBatch,
    MacReservationEndReason,
    MacReservationOrigin,
    MacReservationStatus,
    ReservationPage,
    Secret,
    Snapshot,
    UpdateReservationRequest,
    Volume,
    VolumeState,
    VolumeType,
)

_T = TypeVar("_T")


def _path(value: str) -> str:
    return quote(value, safe="")


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
        if next_cursor is None or next_cursor == current_cursor or next_cursor in seen_cursors:
            return
        seen_cursors.add(next_cursor)
        current_cursor = next_cursor


class AsyncImageService:
    """Image build/pull/list namespace."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def follow_build_logs(
        self,
        image_id: str,
        *,
        on_logs: _LogsCallback,
        on_event: Optional[_EventCallback] = None,
        cursor: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> BuildLogFollowResult:
        """Observe a submitted image build. Timeout is in seconds, not execution TTL."""
        return await follow_build_logs(
            self._transport,
            f"/images/{_path(image_id)}/build-logs-url",
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
        cpu_arch: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Image:
        """Build (or import) an image from an :class:`ImageBuilder` declaration.

        The builder is the only way to say WHAT to build — ``ImageBuilder.
        from_dockerfile(...)``, ``.from_registry(...)``, or the fluent
        ``.run_commands`` / ``.env`` / ``.workdir`` chain — in both SDKs
        (parity with TypeScript's ``images.build(builder, options)``). ``name``
        omitted makes the image anonymous; ``idle_expiry_seconds`` is then its
        idle-retention window — deleted once that long has passed without a
        sandbox being created from it (every create restarts the clock);
        omitted ⇒ the maximum retention supported by the API.
        """
        if not isinstance(builder, ImageBuilder):
            raise TypeError(
                "build() takes an ImageBuilder (ImageBuilder.from_dockerfile(...), "
                ".from_registry(...)); it does not accept a raw dockerfile string"
            )
        resolved_builder = builder
        body: JsonObject = {}
        if name is not None:
            body["name"] = name
        if idle_expiry_seconds is not None:
            body["idle_expiry_seconds"] = idle_expiry_seconds
        if region is not None:
            body["region_id"] = region
        if cpu_arch is not None:
            body["cpu_arch"] = cpu_arch
        source, rendered_dockerfile = resolved_builder.build_request_source()
        if source is not None:
            body["source"] = source
        if rendered_dockerfile is not None:
            body["dockerfile"] = rendered_dockerfile
        created = expect_create_image_resp(
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/images",
                    json_body=body,
                    timeout=timeout or None,
                )
            )
        )
        return await self.get(created.id)

    async def pull(
        self,
        image: str,
        *,
        name: Optional[str] = None,
        region: Optional[str] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Image:
        """Import a registry image, waiting until ready unless ``wait=False``.

        ``timeout`` is the submission and readiness budget in seconds; omitted
        or zero waits without a deadline. A failed import raises
        :class:`ImageBuildFailedError` with the server's recorded error.
        """
        source, _ = ImageBuilder.from_registry(image).build_request_source()
        body: JsonObject = {"source": source}
        if name is not None:
            body["name"] = name
        if region is not None:
            body["region_id"] = region
        return await ImagePull(self._transport, timeout).run(
            RequestSpec("POST", "/images", json_body=body), wait=wait
        )

    async def get(
        self,
        image_id: str,
        *,
        wait: Literal["ready"] | None = None,
        timeout: Optional[float] = None,
    ) -> Image:
        params: Dict[str, JsonValue] = {}
        millis = timeout_millis_from_seconds(timeout) if wait is not None else None
        if millis:
            millis = min(millis, _ARTIFACT_LONGPOLL_MAX_MS)
        if wait is not None:
            params["wait"] = wait
            if millis:
                params["timeout_ms"] = millis
        response = await self._transport.send(
            RequestSpec(
                "GET",
                f"/images/{_path(image_id)}",
                params=params,
                timeout=(millis / 1000 + 5) if millis else None,
                unbounded_read=wait is not None and not millis,
            )
        )
        return parse_image(response)

    async def promote(self, image_id: str, name: str) -> Image:
        """Name an ANONYMOUS image, making it permanent (``PATCH /images/{id}``).

        Promotion only: an image that already has a name answers 409 — renaming
        a named image is not offered (sandboxes reference images by id, so a
        rename would silently relabel every sandbox created from it).
        """
        await self._transport.send(
            RequestSpec("PATCH", f"/images/{_path(image_id)}", json_body={"name": name})
        )
        return await self.get(image_id)

    async def delete(self, image_id: str) -> None:
        await self._transport.send(RequestSpec("DELETE", f"/images/{_path(image_id)}"))

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
        params: Dict[str, JsonValue] = {}
        params["anonymous"] = anonymous
        if platform is not None:
            params["platform"] = platform
        if sandbox_id is not None:
            params["sandbox_id"] = sandbox_id
        if name is not None:
            params["name"] = name
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        response = await self._transport.send(RequestSpec("GET", "/images", params=params))
        page = expect_images_page(response)
        return ListResult([parse_image(item) for item in page.images], page.next_cursor)

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


class AsyncSnapshotService:
    """Sandbox snapshot namespace."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def list(
        self,
        *,
        name: Optional[str] = None,
        sandbox_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ListResult[Snapshot]:
        params: Dict[str, JsonValue] = {}
        if name is not None:
            params["name"] = name
        if sandbox_id is not None:
            params["sandbox_id"] = sandbox_id
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        response = await self._transport.send(RequestSpec("GET", "/snapshots", params=params))
        page = expect_list_snapshots_resp(response)
        return ListResult([parse_snapshot(item) for item in page.snapshots], page.next_cursor)

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
        *,
        wait: Optional[Literal["ready"]] = None,
        timeout: Optional[float] = None,
    ) -> Snapshot:
        params: Dict[str, JsonValue] = {}
        millis = timeout_millis_from_seconds(timeout) if wait is not None else None
        if millis:
            millis = min(millis, _ARTIFACT_LONGPOLL_MAX_MS)
        if wait is not None:
            params["wait"] = wait
            if millis:
                params["timeout_ms"] = millis
        response = await self._transport.send(
            RequestSpec(
                "GET",
                f"/snapshots/{_path(snapshot_id)}",
                params=params,
                timeout=(millis / 1000 + 5) if millis else None,
                unbounded_read=wait is not None and not millis,
            )
        )
        return parse_snapshot(expect_snapshot(response))

    async def delete(self, snapshot_id: str) -> None:
        """Delete a snapshot (409 while sandboxes still depend on it)."""
        await self._transport.send(RequestSpec("DELETE", f"/snapshots/{_path(snapshot_id)}"))


class AsyncVolumeService:
    """Volume management namespace."""

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

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
        params: Dict[str, JsonValue] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        response = await self._transport.send(RequestSpec("GET", "/volumes", params=params))
        page = expect_list_volumes_resp(response)
        return ListResult([_volume(volume) for volume in page.volumes], page.next_cursor)

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

    async def get(self, name_or_id: str, *, create: bool = False, id_only: bool = False) -> Volume:
        """Fetch by id or name; ``id_only`` disables name fallback."""
        params: Dict[str, JsonValue] = {"id_only": True} if id_only else {}
        try:
            return _volume(
                expect_volume(
                    await self._transport.send(
                        RequestSpec("GET", f"/volumes/{_path(name_or_id)}", params=params)
                    )
                )
            )
        except NotFoundError:
            if not create or id_only:
                raise
            return await self.create(name=name_or_id)

    async def create(self, *, name: str, size_gib: Optional[int] = None) -> Volume:
        """Create a volume."""
        body: JsonObject = {"name": name}
        if size_gib is not None:
            body["size_gib"] = size_gib
        return _volume(
            expect_volume(await self._transport.send(RequestSpec("POST", "/volumes", json_body=body)))
        )

    async def delete(self, volume_id: str, *, id_only: bool = False) -> None:
        """Request deletion and return once the API accepts it."""
        params: Dict[str, JsonValue] = {"id_only": True} if id_only else {}
        await self._transport.send(RequestSpec("DELETE", f"/volumes/{_path(volume_id)}", params=params))


class AsyncReservationService:
    """Dedicated-Mac reservations, which macOS sandboxes run on.

    A macOS sandbox runs on a Mac the organization has reserved: reserve one
    here, or let a macOS create buy one (``reservation="new"``, or the
    organization's auto-reserve). A reservation lasts 24-hour periods, each
    charged when it starts. Reserving, renewing and turning auto-renew on need
    macOS access enabled for the organization (otherwise ``403``
    ``FEATURE_NOT_ENABLED``, raised as :class:`FeatureNotEnabledError`);
    listing, reading and turning auto-renew off do not.
    """

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

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
        region.
        """
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

        min_count (default count) is the fewest to accept: with fewer Macs free, the request reserves as many as are free, or none, failing with ConflictError code == "NO_MAC_AVAILABLE" when fewer than min_count are free. The request always carries an Idempotency-Key: idempotency_key when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response returns the same reservations instead of reserving more Macs.
        """
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
            reservations=[_mac_reservation(item) for item in batch.reservations],
        )

    async def list(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> ListResult[MacReservation]:
        """One page of the organization's reservations."""
        params: Dict[str, JsonValue] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        response = await self._transport.send(RequestSpec("GET", "/reservations", params=params))
        page = _expect_model_from_dict(
            response, ReservationPage, "reservation list response", ReservationPage.model_validate
        )
        return ListResult([_mac_reservation(item) for item in page.reservations], page.next_cursor)

    async def iter(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[MacReservation]:
        """Every reservation of the organization, draining page by page."""
        async for item in _iter_pages(
            lambda next_cursor: self.list(cursor=next_cursor, limit=limit),
            cursor,
        ):
            yield item

    async def get(self, reservation_id: str) -> MacReservation:
        return _mac_reservation(
            await self._transport.send(RequestSpec("GET", f"/reservations/{_path(reservation_id)}"))
        )

    async def renew(self, reservation_id: str, *, idempotency_key: Optional[str] = None) -> MacReservation:
        """Renew the reservation for its next period, which the server charges.

        The request always carries an Idempotency-Key: idempotency_key when given, otherwise a random key the SDK generates for this call. Pass your own key if you may retry, so a retry after a lost response does not pay for a second period.
        """
        headers = _idempotency_headers(idempotency_key)
        return _mac_reservation(
            await self._transport.send(
                RequestSpec("POST", f"/reservations/{_path(reservation_id)}/renew", headers=headers)
            )
        )

    async def update(self, reservation_id: str, *, auto_renew: bool) -> MacReservation:
        """Turn the reservation's auto-renewal on or off.

        Turning it on needs macOS access enabled for the organization;
        turning it off works without it, so an organization can stop a reservation
        renewing. Raises :class:`ConflictError` when the reservation is no longer live.
        """
        body: UpdateReservationRequest = {"auto_renew": auto_renew}
        return _mac_reservation(
            await self._transport.send(
                RequestSpec(
                    "PATCH",
                    f"/reservations/{_path(reservation_id)}",
                    json_body=json_object_from_mapping(body),
                )
            )
        )


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
        sandboxes_per_mac=value.sandboxes_per_mac,
        period_n=value.period_n,
        period_seconds=value.period_seconds,
        created_at=value.created_at,
        slots_used=value.slots_used,
        renewal_pending=value.renewal_pending,
        auto_renew=value.auto_renew,
        origin=_mac_reservation_origin(raw_origin),
        raw_origin=raw_origin,
        resources_used=value.resources_used,
        activated_at=value.activated_at,
        capacity=value.capacity,
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
    return {"Idempotency-Key": str(uuid.uuid4()) if idempotency_key is None else idempotency_key}


class AsyncMacModelService:
    """The Mac model registry (``GET /mac-models``).

    Lists the models ``reservations.create(model=...)`` accepts, with each
    model's CPU cores, physical memory, physical SSD capacity and sandbox
    disk room in MiB, maximum sandbox count, and default sandbox size. ``catalog()`` adds free Macs
    and prices.
    """

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def list(self) -> List[MacModel]:
        """Every registered Mac model, ordered by ``id``."""
        response = await self._transport.send(RequestSpec("GET", "/mac-models"))
        page = _expect_model_from_dict(
            response, MacModelList, "Mac model list response", MacModelList.model_validate
        )
        return list(page.mac_models)

    async def catalog(self, *, region: Optional[str] = None) -> MacCatalog:
        """Free Macs and prices per Mac model (GET /mac-catalog). available is advisory; price is null when pricing is unavailable."""
        params: Dict[str, JsonValue] = {} if region is None else {"region_ids": region}
        response = await self._transport.send(RequestSpec("GET", "/mac-catalog", params=params))
        return _expect_model_from_dict(
            response, MacCatalog, "Mac catalog response", MacCatalog.model_validate
        )


class AsyncSecretService:
    """Project secret namespace.

    Project-scoped secrets: named key/value pairs stored
    encrypted in the project. Attach one or more to a sandbox at creation via
    the ``secrets`` create parameter (a list of names). Attached secrets are
    available to outbound HTTPS requests to their allowed domains, not plain
    HTTP requests. Their values never enter the sandbox and are never returned
    by the API (write-only).
    """

    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def list(self, *, cursor: Optional[str] = None, limit: Optional[int] = None) -> ListResult[Secret]:
        """List secret metadata."""
        params: Dict[str, JsonValue] = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        response = await self._transport.send(RequestSpec("GET", "/secrets", params=params))
        payload = expect_list_secrets_resp(response)
        return ListResult(list(payload.secrets), payload.next_cursor)

    async def iter(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[Secret]:
        async for item in _iter_pages(lambda next_cursor: self.list(cursor=next_cursor, limit=limit), cursor):
            yield item

    async def get(self, name: str) -> Secret:
        """Fetch one secret's metadata (never its value)."""
        return expect_secret(await self._transport.send(RequestSpec("GET", f"/secrets/{_path(name)}")))

    async def set(
        self,
        name: str,
        value: str,
        *,
        allowed_domains: Sequence[str],
        description: Optional[str] = None,
        if_not_exists: bool = False,
    ) -> Secret:
        """Create or update a write-only secret.

        ``allowed_domains``: The hosts where this secret's value may be sent
        (exact or ``*.`` wildcard), or ``["*"]`` for any verified host.
        ``"*"`` lets the value reach any HTTPS host, including one a
        compromised sandbox controls.
        """
        body: JsonObject = {"value": value, "allowed_domains": list(allowed_domains)}
        if description is not None:
            body["description"] = description
        headers = {"If-None-Match": "*"} if if_not_exists else {}
        return expect_secret(
            await self._transport.send(
                RequestSpec("PUT", f"/secrets/{_path(name)}", json_body=body, headers=headers)
            )
        )

    async def delete(self, name: str) -> None:
        """Delete a secret."""
        await self._transport.send(RequestSpec("DELETE", f"/secrets/{_path(name)}"))
