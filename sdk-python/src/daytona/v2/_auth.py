# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Final, Optional

import anyio
import httpx

from . import _clock
from ._core import error_from_response
from ._otel import OpenTelemetrySupport
from .errors import ClientTimeoutError, ConnectionError, DaytonaError, create_error

_TOKEN_REFRESH_SKEW_SECONDS = 30.0


def _refresh_skew_seconds(expires_in: float) -> float:
    """Refresh this far before expiry: 30 s, or half the lifetime for short
    TTLs — otherwise a configured TTL <= 30 s would force a fresh
    exchange on every request."""
    return min(_TOKEN_REFRESH_SKEW_SECONDS, expires_in / 2)


_TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_TOKEN_SUBJECT_TYPE = "urn:ietf:params:oauth:token-type:jwt"


@dataclass(frozen=True, slots=True)
class _CachedProjectToken:
    access_token: str = field(repr=False)
    expires_at_monotonic: float
    refresh_skew_seconds: float


class ProjectTokenAuth:
    def __init__(
        self,
        *,
        api_url: str,
        jwt_token: str,
        project_id: str,
        source: str,
        client: httpx.AsyncClient | None = None,
        otel: Optional[OpenTelemetrySupport] = None,
        request_timeout: float = 0,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._request_timeout = request_timeout
        self._jwt_token = jwt_token
        self._project_id = project_id
        self._source = source
        self._client = client
        self._otel = otel
        self._cached: Optional[_CachedProjectToken] = None
        self._lock = anyio.Lock()

    def bind_client(self, client: httpx.AsyncClient) -> None:
        if self._client is None:
            self._client = client

    # SandboxTokenAuth protocol implementation: allows ProjectTokenAuth to be
    # used as an auth_provider on AsyncTransport, so the project
    # token drives the single-flight 401-retry loop the same way the
    # per-sandbox _SandboxTokenAuth does.
    async def bearer(self) -> Optional[str]:
        return await self.get_token()

    async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]:
        fresh = await self.get_token(force_refresh=True, current_token=failed_token)
        return fresh if fresh != failed_token else None

    async def get_token(self, *, force_refresh: bool = False, current_token: Optional[str] = None) -> str:
        cached = self._cached
        if self._can_reuse(cached, force_refresh=force_refresh, current_token=current_token):
            assert cached is not None
            return cached.access_token
        async with self._lock:
            cached = self._cached
            if self._can_reuse(cached, force_refresh=force_refresh, current_token=current_token):
                assert cached is not None
                return cached.access_token
            response = await self._exchange_token()
            expires_at_monotonic = _clock.monotonic() + response.expires_in
            self._cached = _CachedProjectToken(
                access_token=response.access_token,
                expires_at_monotonic=expires_at_monotonic,
                refresh_skew_seconds=_refresh_skew_seconds(response.expires_in),
            )
            return response.access_token

    def _can_reuse(
        self,
        cached: Optional[_CachedProjectToken],
        *,
        force_refresh: bool,
        current_token: Optional[str],
    ) -> bool:
        if cached is None:
            return False
        if cached.expires_at_monotonic - _clock.monotonic() < cached.refresh_skew_seconds:
            return False
        if not force_refresh:
            return True
        return current_token is not None and cached.access_token != current_token

    async def _exchange_token(self) -> _TokenExchange:
        url = f"{self._api_url}/auth/token"
        from ._transport import (
            _request_deadline,
            client_identity_headers,
        )  # local: _transport imports this module

        assert self._client is not None

        headers: Dict[str, str] = {
            **client_identity_headers(self._source),
            "Authorization": f"Bearer {self._jwt_token}",
        }
        span = self._otel.start_request_span(method="POST", url=url, headers=headers) if self._otel else None
        try:
            with _request_deadline(self._request_timeout):
                response = await self._client.request(
                    "POST",
                    url,
                    headers=headers,
                    json={
                        "grant_type": _TOKEN_EXCHANGE_GRANT_TYPE,
                        "subject_token": self._jwt_token,
                        "subject_token_type": _TOKEN_SUBJECT_TYPE,
                        "project_id": self._project_id,
                    },
                )
        except httpx.TimeoutException as error:
            if span is not None:
                span.finish(error=error)
            raise ClientTimeoutError() from error
        except httpx.HTTPError as error:
            if span is not None:
                span.finish(error=error)
            raise ConnectionError(str(error)) from error
        except BaseException as error:
            if span is not None:
                span.finish(error=error)
            raise
        if response.status_code >= 400:
            if span is not None:
                span.finish(status_code=response.status_code)
            raise error_from_response(response.status_code, response.content)
        try:
            exchange = _token_exchange(response.content)
        except BaseException as error:
            if span is not None:
                span.finish(status_code=response.status_code, error=error)
            raise
        if span is not None:
            span.finish(status_code=response.status_code)
        return exchange


# The largest integer both SDKs read exactly (JavaScript's Number.MAX_SAFE_INTEGER).
_MAX_SAFE_INTEGER: Final = 2**53 - 1


@dataclass(frozen=True)
class _TokenExchange:
    """The two token-exchange fields the SDK reads (the token never in its repr)."""

    access_token: str = field(repr=False)
    expires_in: int


def _token_exchange(body: bytes) -> _TokenExchange:
    """The token and its lifetime, or ``BadRequestError`` naming what is wrong.

    Only the fields the SDK reads are checked (``token_type`` and
    ``issued_token_type`` are informational); the messages match the
    TypeScript SDK's for the same response.
    """
    try:
        payload: object = json.loads(body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise _bad_exchange("is not a JSON object")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or access_token == "":
        raise _bad_exchange("access_token must be a non-empty string")
    expires_in = _whole_number(payload.get("expires_in"))
    if expires_in is None or not 0 < expires_in <= _MAX_SAFE_INTEGER:
        raise _bad_exchange("expires_in must be a positive integer")
    return _TokenExchange(access_token=access_token, expires_in=expires_in)


def _whole_number(value: object) -> int | None:
    """A JSON number with an integral value (``60`` or ``60.0``, which JavaScript
    cannot tell apart), as an ``int``; anything else (``60.5``, ``true``) is ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _bad_exchange(problem: str) -> DaytonaError:
    return create_error(f"token exchange response {problem}", 400, "BAD_REQUEST", "SDK")
