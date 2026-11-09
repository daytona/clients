# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The size cap on response bodies the SDK buffers whole (internal).

Every response the transports read into memory (JSON, the in-memory file
reads, multipart bulk reads, error bodies) goes through :func:`read_buffered`:
a declared ``Content-Length`` above the limit is refused before the body is
read, and otherwise reading stops as soon as the bytes read pass it. The
connection is then closed, never returned half-read to the pool. ``0`` means
no limit. Streamed bodies (``download_file``, log, event and build-log streams,
watches) are never held whole and are not capped.
"""

from __future__ import annotations

from typing import Final, Optional

import httpx

from .errors import DaytonaError, create_error

#: The default cap on a buffered response body: 1 GiB.
DEFAULT_MAX_RESPONSE_BYTES: Final = 1 << 30
# The largest integer both SDKs read exactly (JavaScript's Number.MAX_SAFE_INTEGER).
_MAX_SAFE_INTEGER: Final = 2**53 - 1


def validated_max_response_bytes(name: str, value: object) -> int:
    """``value`` as a body limit: an ``int`` from 0 to 2**53 - 1, else ``BadRequestError``."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_SAFE_INTEGER:
        raise create_error(
            f"{name} must be an integer from 0 (no limit) to {_MAX_SAFE_INTEGER}", 400, "BAD_REQUEST", "SDK"
        )
    return value


def resolve_limit(per_call: Optional[int], client_limit: int) -> int:
    """A per-call limit (validated) when given, else the client's."""
    if per_call is None:
        return client_limit
    return validated_max_response_bytes("max_response_bytes", per_call)


def too_large_error(status_code: int, limit: int) -> DaytonaError:
    """The error for a refused body: the status's own error for an error response."""
    if status_code >= 400:
        return create_error(
            f"HTTP {status_code}: the response body was too large to read "
            f"(over the {limit}-byte max_response_bytes limit)",
            status_code,
        )
    return create_error(
        f"the response body is larger than the {limit}-byte max_response_bytes limit; pass a higher "
        "max_response_bytes for this call (0 = no limit), or use download_file() for large files",
        413,
        "PAYLOAD_TOO_LARGE",
        "SDK",
    )


def _declared_too_large(response: httpx.Response, limit: int) -> bool:
    declared = response.headers.get("content-length", "").strip().lstrip("0") or "0"
    if not limit or not declared.isascii() or not declared.isdigit():
        return False
    # Compared by digit count first: a length too long for int() is over any cap.
    bound = str(limit)
    return len(declared) > len(bound) or (len(declared) == len(bound) and declared > bound)


async def read_body(response: httpx.Response, limit: int) -> bytes:
    """Read a streamed response's whole body under ``limit``; always closes it."""
    try:
        if _declared_too_large(response, limit):
            raise too_large_error(response.status_code, limit)
        body = bytearray()
        async for chunk in response.aiter_bytes():
            # Checked before buffering: no byte past the cap is ever held.
            if limit and len(body) + len(chunk) > limit:
                raise too_large_error(response.status_code, limit)
            body.extend(chunk)
        return bytes(body)
    finally:
        await response.aclose()


def read_body_sync(response: httpx.Response, limit: int) -> bytes:
    """:func:`read_body` for a blocking client."""
    try:
        if _declared_too_large(response, limit):
            raise too_large_error(response.status_code, limit)
        body = bytearray()
        for chunk in response.iter_bytes():
            # Checked before buffering: no byte past the cap is ever held.
            if limit and len(body) + len(chunk) > limit:
                raise too_large_error(response.status_code, limit)
            body.extend(chunk)
        return bytes(body)
    finally:
        response.close()


_UNREAD_HEADERS: Final = frozenset({b"content-encoding", b"content-length", b"transfer-encoding"})


def buffered(response: httpx.Response, body: bytes) -> httpx.Response:
    """A complete response holding ``body`` (already decoded) in place of the stream."""
    headers = [(name, value) for name, value in response.headers.raw if name.lower() not in _UNREAD_HEADERS]
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=body,
        request=response.request,
        extensions=response.extensions,
    )


async def send_buffered(client: httpx.AsyncClient, request: httpx.Request, limit: int) -> httpx.Response:
    """Send ``request`` and buffer its body under ``limit``."""
    response = await client.send(request, stream=True)
    return buffered(response, await read_body(response, limit))


def send_buffered_sync(client: httpx.Client, request: httpx.Request, limit: int) -> httpx.Response:
    """:func:`send_buffered` for a blocking client."""
    response = client.send(request, stream=True)
    return buffered(response, read_body_sync(response, limit))
