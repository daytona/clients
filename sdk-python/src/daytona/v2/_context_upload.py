# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Direct build-context uploads (``POST /build-contexts``).

The API declares one upload, hands back presigned part URLs for an object key
it chose, and verifies the stored archive when the upload is completed. The
part URLs are bearer credentials: they never reach a log, an exception message
or a ``repr``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterator, Mapping, Optional, Sequence, Union
from urllib.parse import quote

import anyio
import httpx

from ._core import RequestSpec
from ._transport import AsyncTransport
from .errors import (
    BadRequestError,
    ClientTimeoutError,
    ConnectionError,
    DaytonaError,
    ServiceUnavailableError,
)
from .types import JsonObject

BuildContext = Union[bytes, bytearray, memoryview, str, "os.PathLike[str]"]
"""A build-context tar: its bytes, or the path of a ``.tar`` file."""

MAX_CONTEXT_BYTES = 1 << 30
_HASH_CHUNK = 1 << 20
_PART_CHUNK = 1 << 20
_PART_ATTEMPTS = 3
_PART_TIMEOUT_SECONDS = 300.0
# Parts in flight at once; each streams one chunk at a time, so memory stays a
# few chunks whatever the part size.
_PART_CONCURRENCY = 4
_COMPLETE_ATTEMPTS = 3
# The API reads the whole stored archive back (up to 1 GiB) before it answers.
_COMPLETE_TIMEOUT_SECONDS = 600.0
_REDACTED_URL = "<build context part URL>"
# The pause between attempts; tests replace it.
_sleep = anyio.sleep


class _RedactPartUrls(logging.Filter):
    """Replaces the part URLs httpx logs ("HTTP Request: PUT <url> ...") while they are in use."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._urls: dict[str, int] = {}

    @contextmanager
    def active(self, urls: Sequence[str]) -> Iterator[None]:
        keys = [form for url in urls for form in _logged_forms(url)]
        with self._lock:
            for key in keys:
                self._urls[key] = self._urls.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                for key in keys:
                    if self._urls[key] == 1:
                        del self._urls[key]
                    else:
                        self._urls[key] -= 1

    def filter(self, record: logging.LogRecord) -> bool:
        if self._urls and isinstance(record.args, tuple):
            with self._lock:
                urls = set(self._urls)
            record.args = tuple(
                _REDACTED_URL if isinstance(arg, (str, httpx.URL)) and str(arg) in urls else arg
                for arg in record.args
            )
        return True


def _logged_forms(url: str) -> set[str]:
    # httpx logs the URL as it parsed it, which may differ in encoding. An
    # unparsable URL fails its request before httpx logs anything.
    try:
        return {url, str(httpx.URL(url))}
    except (httpx.InvalidURL, TypeError, ValueError):
        return {url}


_redact_part_urls = _RedactPartUrls()
logging.getLogger("httpx").addFilter(_redact_part_urls)


class _Source:
    """The archive, streamed by offset so a file is held one chunk at a time, never a whole part."""

    def __init__(self, context: BuildContext) -> None:
        if isinstance(context, (bytes, bytearray, memoryview)):
            # A view, not a copy, so a 1 GiB buffer is not duplicated; only a
            # non-contiguous view, which cannot be cast to bytes, is copied.
            view = memoryview(context)
            if not view.c_contiguous:
                view = memoryview(view.tobytes())
            self._data: Optional[memoryview] = view.cast("B")
            self._path: Optional[Path] = None
            self.size = self._data.nbytes
        elif isinstance(context, (str, os.PathLike)):
            self._data = None
            self._path = Path(context)
            if not self._path.is_file():
                raise BadRequestError(
                    f"build context {str(self._path)!r} is not a file; pass a tar archive",
                    status_code=400,
                    code="BAD_REQUEST",
                    source="SDK",
                )
            self.size = self._path.stat().st_size
        else:
            raise TypeError("context must be the bytes of a tar archive or the path of a tar file")
        if self.size == 0:
            raise BadRequestError("build context is empty", status_code=400, code="BAD_REQUEST", source="SDK")
        if self.size > MAX_CONTEXT_BYTES:
            raise BadRequestError(
                f"build context exceeds the 1 GiB limit ({self.size} bytes)",
                status_code=400,
                code="BAD_REQUEST",
                source="SDK",
            )

    async def chunks(self, offset: int, length: int) -> AsyncIterator[bytes]:
        """Yield ``length`` bytes from ``offset`` in chunks of at most ``_PART_CHUNK``."""
        end = offset + length
        if self._data is not None:
            for start in range(offset, end, _PART_CHUNK):
                yield bytes(self._data[start : min(start + _PART_CHUNK, end)])
            return
        assert self._path is not None
        handle = await anyio.to_thread.run_sync(self._path.open, "rb")
        try:
            await anyio.to_thread.run_sync(handle.seek, offset)
            remaining = length
            while remaining > 0:
                chunk = await anyio.to_thread.run_sync(handle.read, min(_PART_CHUNK, remaining))
                if not chunk:
                    raise _changed_while_uploading()
                remaining -= len(chunk)
                yield chunk
        finally:
            await anyio.to_thread.run_sync(handle.close)

    def sha256(self) -> str:
        digest = hashlib.sha256()
        if self._data is not None:
            digest.update(self._data)
        else:
            assert self._path is not None
            with self._path.open("rb") as handle:
                while chunk := handle.read(_HASH_CHUNK):
                    digest.update(chunk)
        return digest.hexdigest()


@dataclass(frozen=True)
class _Part:
    number: int
    bytes: int
    url: str

    def __repr__(self) -> str:
        return f"_Part(number={self.number}, bytes={self.bytes}, url=<redacted>)"


def _parts(payload: Mapping[str, object]) -> list[_Part]:
    raw = payload.get("parts")
    if not isinstance(raw, list):
        raise DaytonaError("build context upload response has no parts", source="SDK")
    parts: list[_Part] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise DaytonaError("build context upload response has a malformed part", source="SDK")
        number, size, url = item.get("part_number"), item.get("bytes"), item.get("url")
        if not isinstance(number, int) or not isinstance(size, int) or not isinstance(url, str):
            raise DaytonaError("build context upload response has a malformed part", source="SDK")
        parts.append(_Part(number, size, url))
    return parts


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value == "":
        raise DaytonaError(f"build context upload response has no {key}", source="SDK")
    return value


def _changed_while_uploading() -> BadRequestError:
    return BadRequestError(
        "build context changed while it was being uploaded",
        status_code=400,
        code="BAD_REQUEST",
        source="SDK",
    )


async def _put_part(client: httpx.AsyncClient, source: _Source, offset: int, part: _Part) -> None:
    status: Optional[int] = None
    for attempt in range(_PART_ATTEMPTS):
        try:
            # A presigned PUT needs a fixed length, so the stream is not chunked.
            response = await client.put(
                part.url,
                content=source.chunks(offset, part.bytes),
                headers={"Content-Length": str(part.bytes)},
            )
        except httpx.HTTPError:
            # The exception text names the URL, so it is not chained.
            status = None
        else:
            status = response.status_code
            if 200 <= status < 300:
                return
            if status < 500 and status not in (408, 429):
                raise BadRequestError(
                    f"storage refused build context part {part.number} (HTTP {status}); "
                    "its upload URL may have expired, start the build again",
                    status_code=400,
                    code="BAD_REQUEST",
                    source="SDK",
                )
        if attempt + 1 < _PART_ATTEMPTS:
            await _sleep(0.5 * 2**attempt)
    if status is None:
        raise ConnectionError(
            f"build context part {part.number} could not reach storage", source="SDK"
        ) from None
    raise ServiceUnavailableError(
        f"storage did not accept build context part {part.number} (HTTP {status})",
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        source="SDK",
    )


async def upload_build_context(
    transport: AsyncTransport,
    context: BuildContext,
    *,
    dockerfile: str,
    region: Optional[str],
) -> tuple[str, str]:
    """Upload ``context``; return the ID and region of its verified upload."""
    if dockerfile.strip() == "":
        raise BadRequestError(
            "dockerfile must be a non-empty path", status_code=400, code="BAD_REQUEST", source="SDK"
        )
    source = _Source(context)
    sha256 = await anyio.to_thread.run_sync(source.sha256)
    body: JsonObject = {"dockerfile": dockerfile, "bytes": source.size, "sha256": sha256}
    if region is not None:
        body["region_id"] = region
    created = await transport.send(RequestSpec("POST", "/build-contexts", json_body=body))
    if not isinstance(created, Mapping):
        raise DaytonaError("build context upload response is not an object", source="SDK")
    upload_id = _text(created, "id")
    region_id = _text(created, "region_id")
    if created.get("state") == "ready":
        return upload_id, region_id
    parts = _parts(created)
    if (
        any(part.number != index + 1 or part.bytes <= 0 for index, part in enumerate(parts))
        or sum(part.bytes for part in parts) != source.size
    ):
        raise DaytonaError("build context upload parts do not cover the archive", source="SDK")
    with _redact_part_urls.active([part.url for part in parts]):
        async with httpx.AsyncClient(
            timeout=_PART_TIMEOUT_SECONDS, follow_redirects=False, trust_env=True
        ) as client:
            await _put_parts(client, source, parts)
    await _complete(transport, upload_id, region_id)
    return upload_id, region_id


async def _put_parts(client: httpx.AsyncClient, source: _Source, parts: Sequence[_Part]) -> None:
    """Upload every part, at most ``_PART_CONCURRENCY`` at a time; the first failure cancels the rest."""
    limiter = anyio.Semaphore(_PART_CONCURRENCY)
    failures: list[Exception] = []

    async def put(part: _Part, offset: int, scope: anyio.CancelScope) -> None:
        async with limiter:
            try:
                await _put_part(client, source, offset, part)
            except Exception as error:
                failures.append(error)
                scope.cancel()

    async with anyio.create_task_group() as group:
        offset = 0
        for part in parts:
            group.start_soon(put, part, offset, group.cancel_scope)
            offset += part.bytes
    if failures:
        raise failures[0]


def _retryable_complete(error: DaytonaError) -> bool:
    if isinstance(error, (ClientTimeoutError, ConnectionError)):
        return True
    return error.status_code is not None and error.status_code >= 500 and error.status_code != 501


async def _complete(transport: AsyncTransport, upload_id: str, region_id: str) -> None:
    """Verify the upload. A repeated complete of a verified upload answers with it, so failures are retried."""
    configured = transport.request_timeout_seconds
    spec = RequestSpec(
        "POST",
        f"/build-contexts/{quote(upload_id, safe='')}/complete",
        json_body={"region_id": region_id},
        timeout=None if configured == 0 else max(configured, _COMPLETE_TIMEOUT_SECONDS),
    )
    for attempt in range(_COMPLETE_ATTEMPTS):
        try:
            await transport.send(spec)
            return
        except DaytonaError as error:
            if attempt + 1 >= _COMPLETE_ATTEMPTS or not _retryable_complete(error):
                raise
        await _sleep(2.0**attempt)
