# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import quote

from ._api_core import (
    expect_daytona_preview_url,
    expect_endpoint_list_resp,
    expect_preview_endpoint,
    expect_signed_preview_url_resp,
)
from ._core import (
    INT32_MAX,
    JSON_MAX_DEPTH,
    RequestSpec,
    _nested_deeper_than,
    expect_is_port_in_use_response,
    expect_port_list,
    page_params,
    parse_timestamp,
    preview_port,
)
from ._gen.api import SandboxPort as PreviewEndpointDto
from ._transport import AsyncTransport
from .errors import create_error
from .types import ListResult, PreviewEndpoint, PreviewLink, SandboxAccessToken, SignedPreviewLink

# Request header (and its value) that skips the preview warning page. Send it
# when a headless browser or another non-interactive client opens a preview URL.
PREVIEW_WARNING_SKIP_HEADER = "X-Daytona-Skip-Preview-Warning"
PREVIEW_WARNING_SKIP_HEADER_VALUE = "true"

_JWT_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _preview_token_expiry(token: str) -> Optional[datetime]:
    """Read the standard JWT expiry claim as advisory metadata only.

    Token contents never determine its permissions or whether to mint a
    port-scoped replacement. Opaque or unrepresentable expiry stays unknown.
    """
    parts = token.split(".")
    if len(parts) != 3 or any(not part or _JWT_SEGMENT.fullmatch(part) is None for part in parts):
        return None
    try:
        raw = base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (binascii.Error, json.JSONDecodeError, RecursionError, UnicodeError, ValueError):
        return None
    # Nested deeper than every JSON body may be: not a token to read.
    if _nested_deeper_than(payload, JSON_MAX_DEPTH):
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return None
    try:
        if not math.isfinite(exp) or exp <= 0:
            return None
        # Whole milliseconds, floored, exactly as the TypeScript SDK does.
        ms = math.floor(exp * 1000)
        expires = datetime.fromtimestamp(ms // 1000, tz=timezone.utc) + timedelta(milliseconds=ms % 1000)
    except (OverflowError, OSError, ValueError):
        return None
    return expires


def _assert_port(port: int) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise create_error(f"port must be an integer in 1..65535, got {port!r}", 400, "BAD_REQUEST", "SDK")


def _assert_signed_preview_ttl(expires_in_seconds: int) -> None:
    if (
        not isinstance(expires_in_seconds, int)
        or isinstance(expires_in_seconds, bool)
        or expires_in_seconds <= 0
    ):
        raise create_error("expires_in_seconds must be a positive integer", 400, "BAD_REQUEST", "SDK")


def _port_segment(port: int) -> str:
    if not isinstance(port, int) or isinstance(port, bool):
        raise create_error(f"port must be an integer, got {port!r}", 400, "BAD_REQUEST", "SDK")
    return quote(str(port), safe="")


def _preview_endpoint_from_api(payload: PreviewEndpointDto) -> PreviewEndpoint:
    return PreviewEndpoint(
        port=payload.port,
        public=payload.public,
        url=payload.url,
        created_at=parse_timestamp(payload.created_at),
    )


class AsyncPortsService:
    def __init__(
        self,
        _transport: Optional[AsyncTransport],
        _api: Optional[AsyncTransport] = None,
        *,
        _sandbox_id: Optional[str] = None,
        _mint_preview_token: Optional[Callable[[int], Awaitable[Optional[SandboxAccessToken]]]] = None,
    ) -> None:
        self._transport = _transport
        self._api = _api
        self._sandbox_id = _sandbox_id
        # Sandbox-provided minter for a ports-scoped sandbox access token.
        # Returns None when the handle cannot
        # mint (caller-supplied token, no key, route unavailable).
        self._mint_preview_token = _mint_preview_token

    async def list(self) -> ListResult[int]:
        payload = expect_port_list(await self._require_toolbox().send(RequestSpec("GET", "/port")))
        # The contract types each port as an int32 and declares no range.
        for index, port in enumerate(payload.ports):
            if not -INT32_MAX - 1 <= port <= INT32_MAX:
                raise create_error(
                    f"port list response has a ports[{index}] that is not a 32-bit integer",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )
        return ListResult(items=list(payload.ports))

    async def is_in_use(self, port: int, /) -> bool:
        """Whether ``port`` currently has a listener.

        A port outside 1–65535 is refused with
        :class:`~daytona.v2.errors.BadRequestError` before
        any request. The sandbox answers only for ports 3000–9999 and refuses
        any other with ``BAD_REQUEST``, so a port such as 80 raises rather than
        returning a ``False`` that reads like "nothing is listening there".
        """
        _assert_port(port)
        payload = expect_is_port_in_use_response(
            await self._require_toolbox().send(RequestSpec("GET", f"/port/{_port_segment(port)}/in-use"))
        )
        return payload.is_in_use

    async def list_endpoints(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> ListResult[PreviewEndpoint]:
        """One page of the sandbox's preview endpoints.

        A sandbox that is deleted or being deleted raises
        :class:`~daytona.v2.errors.ConflictError` (``CONFLICT``).
        """
        params = page_params(cursor, limit)
        payload = expect_endpoint_list_resp(
            await self._require_api().send(RequestSpec("GET", self._ports_path(), params=params))
        )
        return ListResult(
            items=[_preview_endpoint_from_api(item) for item in payload.ports],
            next_cursor=payload.next_cursor,
        )

    async def iter_endpoints(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None
    ) -> AsyncIterator[PreviewEndpoint]:
        seen: set[str] = set()
        current = cursor
        if current is not None:
            seen.add(current)
        while True:
            page = await self.list_endpoints(cursor=current, limit=limit)
            for item in page.items:
                yield item
            if not page.next_cursor or page.next_cursor == current or page.next_cursor in seen:
                return
            seen.add(page.next_cursor)
            current = page.next_cursor

    async def expose(self, port: int, /, *, public: bool = False) -> PreviewEndpoint:
        """Register a sandbox port as a preview endpoint.

        If the port is already exposed, returns the existing endpoint (updating
        its visibility if ``public`` differs from the current value; the change
        takes effect immediately on event delivery, at most 30 s if an event is
        missed). Public endpoints need no credential. Programmatic access to a private one
        needs the token from :meth:`preview_link` (see
        :class:`~daytona.v2.types.PreviewLink`) or a :meth:`signed_preview_link`
        URL.
        A sandbox that is deleted or being deleted raises
        :class:`~daytona.v2.errors.ConflictError` (``CONFLICT``).

        :param port: TCP port inside the sandbox (1–65535, except the sandbox
            daemon's reserved ports 2280, 2281 and 22220).
        :param public: When ``True`` the endpoint is publicly accessible
            without any credential.
        :returns: :class:`~daytona.v2.types.PreviewEndpoint` with the URL.
        """
        preview_port(port)
        if not isinstance(public, bool):
            raise create_error("public must be a boolean", 400, "BAD_REQUEST", "SDK")
        payload = expect_preview_endpoint(
            await self._require_api().send(
                RequestSpec("PUT", self._port_path(port), json_body={"public": public})
            )
        )
        return _preview_endpoint_from_api(payload)

    async def preview_link(self, port: int, /) -> PreviewLink:
        """Preview URL plus access token for one sandbox port.

        Callers send the token in the ``X-Daytona-Preview-Token`` header.
        Public endpoints need no token (``token == ""``). The
        returned :class:`~daytona.v2.types.PreviewLink` also carries
        ``expires_at`` when the token's expiry is known. For private
        endpoints, a handle permitted to mint gets a short-lived sandbox
        access token scoped to exactly this
        port. Otherwise the preview-url token is passed through unchanged.
        Token contents are not used to infer access permissions.

        A port that is not exposed yet is exposed first (privately, as
        :meth:`expose` does): this call registers a preview endpoint. The
        sandbox daemon's ports 2280, 2281 and 22220 are never preview ports:
        they are refused with :class:`~daytona.v2.errors.BadRequestError`
        before any request, as by :meth:`expose` and
        :meth:`signed_preview_link`. A sandbox that is deleted or being
        deleted raises :class:`~daytona.v2.errors.ConflictError`
        (``CONFLICT``).
        """
        preview_port(port)
        try:
            resolved = _preview_endpoint_from_api(
                expect_preview_endpoint(
                    await self._require_api().send(RequestSpec("GET", self._port_path(port)))
                )
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            resolved = await self.expose(port)
        if resolved.public:
            return PreviewLink(url=resolved.url, token="")
        payload = expect_daytona_preview_url(
            await self._require_api().send(RequestSpec("POST", f"{self._port_path(port)}/preview-url"))
        )
        token = payload.token
        expires_at = _preview_token_expiry(token)
        if self._mint_preview_token is not None:
            try:
                minted = await self._mint_preview_token(port)
            except Exception:  # any mint failure keeps the route token
                # The preview-url route above already returned a usable token;
                # a failed port-scoped mint (a transient 5xx, a just-stopped
                # sandbox) must not discard it.
                minted = None
            if minted is not None:
                token = minted.access_token
                expires_at = minted.expires_at
        return PreviewLink(url=payload.url or resolved.url, token=token, expires_at=expires_at)

    async def signed_preview_link(
        self, port: int, /, *, expires_in_seconds: Optional[int] = None
    ) -> SignedPreviewLink:
        """Return a self-contained signed URL for one sandbox port.

        The token is embedded in the hostname, so no extra headers or credentials
        are needed — the right choice for handing a URL to a browser, a tool,
        or any client that cannot set request headers.

        The default lifetime is 60 seconds. Pass a longer ``expires_in_seconds``
        for multi-step use (the deployment caps the maximum, typically 7 days).
        Revoke the link early with :meth:`expire_signed_preview_link`, which
        revokes every signed preview link on the sandbox.
        A sandbox that is deleted or being deleted raises
        :class:`~daytona.v2.errors.ConflictError` (``CONFLICT``).

        :param port: TCP port inside the sandbox (1–65535, except the sandbox
            daemon's reserved ports 2280, 2281 and 22220).
        :param expires_in_seconds: Requested token lifetime in seconds (positive
            integer). Omit for the server default (60 s).
        :returns: :class:`~daytona.v2.types.SignedPreviewLink` with ``url``,
            ``token``, and ``expires_at``.

        Example::

            link = await sandbox.ports.signed_preview_link(8080, expires_in_seconds=300)
            # Hand link.url to a browser or another tool — no headers needed.
        """
        preview_port(port)
        if expires_in_seconds is not None:
            _assert_signed_preview_ttl(expires_in_seconds)
        body = {} if expires_in_seconds is None else {"expires_in_seconds": expires_in_seconds}
        payload = expect_signed_preview_url_resp(
            await self._require_api().send(
                RequestSpec(
                    "POST",
                    f"{self._port_path(port)}/signed-preview-urls",
                    json_body=body,
                )
            )
        )
        # Required by the contract, so the model always carries it; without an
        # offset it is UTC, as parse_timestamp reads it.
        expires_at = payload.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return SignedPreviewLink(url=payload.url, token=payload.token, expires_at=expires_at)

    async def expire_signed_preview_link(self, port: int, token: str, /) -> None:
        """Revoke the sandbox's signed preview links before their natural expiry.

        Expiring a link revokes every signed preview link on the sandbox, on
        every port, not only the one ``token`` names. A token that is not a live
        link for this sandbox and port changes nothing and raises no error.
        Unlike minting, revoking still works while the sandbox is being deleted.

        :param port: TCP port the link was issued for.
        :param token: The ``token`` field from :class:`~daytona.v2.types.SignedPreviewLink`.
        """
        _assert_port(port)
        if not isinstance(token, str) or not token.strip():
            raise create_error("token must be a non-empty string", 400, "BAD_REQUEST", "SDK")
        await self._require_api().send(
            RequestSpec(
                "POST",
                f"{self._port_path(port)}/signed-preview-urls/revoke",
                json_body={"token": token},
            )
        )

    async def unexpose(self, port: int, /) -> None:
        """Remove a port's preview endpoint; this still works while the sandbox is being deleted."""
        _assert_port(port)
        await self._require_api().send(RequestSpec("DELETE", self._port_path(port)))

    def _require_api(self) -> AsyncTransport:
        if self._api is None:
            raise create_error(
                "sandbox handle has no API transport for preview endpoint operations",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return self._api

    def _require_toolbox(self) -> AsyncTransport:
        if self._transport is None:
            raise create_error(
                "sandbox handle has no toolbox URL; fetch the sandbox with get(id) (or pass toolbox_url)",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return self._transport

    def _require_sandbox_id(self) -> str:
        if self._sandbox_id is None:
            raise create_error(
                "sandbox handle has no sandbox id for preview endpoint operations",
                400,
                "BAD_REQUEST",
                "SDK",
            )
        return self._sandbox_id

    def _ports_path(self) -> str:
        return f"/sandboxes/{quote(self._require_sandbox_id(), safe='')}/ports"

    def _port_path(self, port: int) -> str:
        return f"{self._ports_path()}/{_port_segment(port)}"
