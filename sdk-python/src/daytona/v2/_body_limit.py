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

import zlib
from typing import AsyncIterator, Final, Iterator, Optional

import httpx

from ._core import MAX_SAFE_INTEGER
from .errors import ConnectionError, DaytonaError, create_error, redirect_error

#: The default cap on a buffered response body: 1 GiB.
DEFAULT_MAX_RESPONSE_BYTES: Final = 1 << 30
# The largest integer both SDKs read exactly (JavaScript's Number.MAX_SAFE_INTEGER).


def validated_max_response_bytes(name: str, value: object) -> int:
    """``value`` as a body limit: an ``int`` from 0 to 2**53 - 1, else ``BadRequestError``."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SAFE_INTEGER:
        raise create_error(
            f"{name} must be an integer from 0 (no limit) to {MAX_SAFE_INTEGER}", 400, "BAD_REQUEST", "SDK"
        )
    return value


def resolve_limit(per_call: Optional[int], client_limit: int) -> int:
    """A per-call limit (validated) when given, else the client's."""
    if per_call is None:
        return client_limit
    return validated_max_response_bytes("max_response_bytes", per_call)


def too_large_error(status_code: int, limit: int) -> DaytonaError:
    """The error for a refused body: the status's own error for an error response."""
    if 300 <= status_code < 400:
        return redirect_error(status_code)
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


#: The ``Accept-Encoding`` the SDK's clients send: the encodings its capped
#: readers decode with bounded output. httpx would add ``br`` / ``zstd`` when
#: their packages are installed, whose decoders cannot bound one chunk's output.
ACCEPT_ENCODING: Final = "gzip, deflate"
_BOUNDED_ENCODINGS: Final = frozenset({"gzip", "x-gzip", "deflate"})
#: The most decoded bytes one decoding step produces.
DECODE_STEP: Final = 64 * 1024


def bounded_accept_encoding(value: Optional[str]) -> bool:
    """Whether an ``Accept-Encoding`` asks only for encodings the SDK can bound."""
    if value is None:
        return False
    tokens = {token.split(";")[0].strip().lower() for token in value.split(",")} - {""}
    return tokens <= (_BOUNDED_ENCODINGS | {"identity"})


def _unreadable(status_code: int, detail: str) -> DaytonaError:
    if 300 <= status_code < 400:
        return redirect_error(status_code)
    if status_code >= 400:
        return create_error(
            f"HTTP {status_code}: the response body could not be read ({detail})", status_code
        )
    return create_error(f"the response body {detail}", 400, "BAD_REQUEST", "SDK")


_GZIP_MAGIC: Final = b"\x1f\x8b"
_ZLIB_HEADER_BYTES: Final = 2


class _BoundedDecoder:
    """Decodes a gzip or deflate body incrementally, at most ``DECODE_STEP``
    decoded bytes at a time, so a small compressed chunk never expands into a
    large decoded one (a decompression bomb) before the cap is checked.

    A body whose compressed data stops before its end is refused, as a body
    cut short on the wire is. Gzip members that follow one another are all
    decoded; other bytes after the last member (or after a deflate stream) are
    ignored, as httpx and Node's zlib do.
    """

    def __init__(self, encoding: str, status_code: int) -> None:
        self._encoding = encoding
        self._status_code = status_code
        self._gzip = encoding in ("gzip", "x-gzip")
        self._first = True
        self._fed = False
        # Bytes after a finished gzip member, too few yet to tell whether
        # another member starts; ``None`` while a member is being decoded.
        self._between: Optional[bytes] = None
        self._trailing = False
        # A deflate body's first bytes, held until there are two: the zlib
        # header is two bytes, and only both tell a zlib-wrapped stream from
        # a raw one, however the network splits the body.
        self._head: Optional[bytes] = None if self._gzip else b""
        self._z = self._new()

    def _new(self) -> "zlib._Decompress":  # noqa: UP037
        return zlib.decompressobj(16 + zlib.MAX_WBITS if self._gzip else zlib.MAX_WBITS)

    def _decompress(self, data: bytes) -> bytes:
        try:
            out = self._z.decompress(data, DECODE_STEP)
        except zlib.error:
            if self._first and not self._gzip:
                # A raw deflate stream (no zlib header), as httpx accepts.
                self._first = False
                self._z = zlib.decompressobj(-zlib.MAX_WBITS)
                return self._decompress(data)
            raise _unreadable(self._status_code, f"is not valid {self._encoding} data") from None
        self._first = False
        return out

    def _after_end(self, rest: bytes) -> Optional[bytes]:
        """What to decode next once a member or stream has ended: the next
        gzip member, or ``None`` (wait for more bytes, or ignore the rest)."""
        if not self._gzip:
            self._trailing = True
            return None
        if len(rest) < len(_GZIP_MAGIC) and _GZIP_MAGIC.startswith(rest):
            self._between = rest
            return None
        self._between = None
        if not rest.startswith(_GZIP_MAGIC):
            self._trailing = True
            return None
        self._z = self._new()
        return rest

    def feed(self, data: bytes) -> Iterator[bytes]:
        if not data or self._trailing:
            return
        self._fed = True
        if self._head is not None:
            self._head += data
            if len(self._head) < _ZLIB_HEADER_BYTES:
                return
            data, self._head = self._head, None
        pending: Optional[bytes] = data
        if self._between is not None:
            pending = self._after_end(self._between + data)
        while pending is not None:
            out = self._decompress(pending)
            if out:
                yield out
            if self._z.eof:
                pending = self._after_end(self._z.unused_data)
                continue
            pending = self._z.unconsumed_tail
            if not pending and len(out) < DECODE_STEP:
                return

    def finish(self) -> Iterator[bytes]:
        if not self._fed or self._trailing or self._between is not None:
            return
        if not self._z.eof:
            # The compressed data stops before its end: as incomplete as a
            # body cut short on the wire, whatever the status.
            raise ConnectionError(f"the response body ended before its {self._encoding} data was complete")
        yield from ()


def _decoder(response: httpx.Response) -> Optional[_BoundedDecoder]:
    """The bounded decoder for the response's ``Content-Encoding``, ``None`` for
    none (or ``identity``); any other encoding is refused without decoding."""
    tokens = [
        token.strip().lower()
        for token in response.headers.get("content-encoding", "").split(",")
        if token.strip() and token.strip().lower() != "identity"
    ]
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0] in _BOUNDED_ENCODINGS:
        return _BoundedDecoder(tokens[0], response.status_code)
    raise _unreadable(response.status_code, f"uses an unrequested content encoding: {', '.join(tokens)}")


class _Collector:
    """The decoded body, checked against ``limit`` before each piece is kept."""

    def __init__(self, response: httpx.Response, limit: int, *, decode: bool) -> None:
        self._response = response
        self._limit = limit
        self.body = bytearray()
        self._decoder = _decoder(response) if decode else None

    def add(self, raw: bytes) -> None:
        for piece in self._decoder.feed(raw) if self._decoder is not None else (raw,):
            self._keep(piece)

    def finish(self) -> bytes:
        if self._decoder is not None:
            for piece in self._decoder.finish():
                self._keep(piece)
        return bytes(self.body)

    def _keep(self, piece: bytes) -> None:
        # Checked before buffering: no byte past the cap is ever held, and a
        # piece is at most one bounded decoding step.
        if self._limit and len(self.body) + len(piece) > self._limit:
            raise too_large_error(self._response.status_code, self._limit)
        self.body.extend(piece)


def _start(response: httpx.Response, limit: int) -> _Collector:
    if _declared_too_large(response, limit):
        raise too_large_error(response.status_code, limit)
    if response.is_stream_consumed:
        # Built in memory with its (decoded) content, as a test double is:
        # only the cap applies.
        collector = _Collector(response, limit, decode=False)
        collector.add(response.content)
        return collector
    return _Collector(response, limit, decode=True)


async def _raw_async(response: httpx.Response) -> AsyncIterator[bytes]:
    async for data in response.aiter_raw():
        yield data


async def decoded_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    """A streamed body's decoded bytes as they arrive, never decoded by httpx:
    a gzip or deflate body in steps of at most ``DECODE_STEP`` bytes, so a
    stream's readers bound what they hold as the bytes arrive; any other
    encoding is refused undecoded, as for a capped read."""
    if response.is_stream_consumed:
        # Built in memory with its (decoded) content, as a test double is.
        if response.content:
            yield response.content
        return
    decoder = _decoder(response)
    async for raw in _raw_async(response):
        if decoder is None:
            yield raw
            continue
        for piece in decoder.feed(raw):
            yield piece
    if decoder is not None:
        for piece in decoder.finish():
            yield piece


async def read_body(response: httpx.Response, limit: int) -> bytes:
    """Read a streamed response's whole body under ``limit``; always closes it.

    The wire bytes are decoded here, in bounded steps, never by httpx.
    """
    try:
        collector = _start(response, limit)
        if not response.is_stream_consumed:
            async for raw in _raw_async(response):
                collector.add(raw)
        return collector.finish()
    finally:
        await response.aclose()


def read_body_sync(response: httpx.Response, limit: int) -> bytes:
    """:func:`read_body` for a blocking client."""
    try:
        collector = _start(response, limit)
        if not response.is_stream_consumed:
            for raw in response.iter_raw():
                collector.add(raw)
        return collector.finish()
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
    _bound_accept_encoding(request)
    response = await client.send(request, stream=True)
    return buffered(response, await read_body(response, limit))


def send_buffered_sync(client: httpx.Client, request: httpx.Request, limit: int) -> httpx.Response:
    """:func:`send_buffered` for a blocking client."""
    _bound_accept_encoding(request)
    response = client.send(request, stream=True)
    return buffered(response, read_body_sync(response, limit))


def _bound_accept_encoding(request: httpx.Request) -> None:
    """Ask only for encodings the capped reader decodes with bounded output
    (a client the caller injected may advertise more)."""
    if not bounded_accept_encoding(request.headers.get("accept-encoding")):
        request.headers["Accept-Encoding"] = ACCEPT_ENCODING
