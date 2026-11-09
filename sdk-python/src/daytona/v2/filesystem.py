# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import secrets
import stat
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import (
    AsyncIterator,
    Awaitable,
    BinaryIO,
    Callable,
    Dict,
    Final,
    Mapping,
    NamedTuple,
    Optional,
    Protocol,
    Sequence,
    TypeVar,
)
from urllib.parse import quote

from ._body_limit import validated_max_response_bytes
from ._core import (
    RequestSpec,
    TransportPayload,
    bulk_download_spec,
    expect_file_info,
    expect_object,
    file_bulk_upload_spec,
    file_download_spec,
    file_upload_spec,
    list_shape,
    parse_bulk_read_result,
    parse_bulk_write_result,
    parse_sse_lines_async,
)
from ._transport import AsyncTransport
from .errors import ConnectionError, TransferCancelledError, create_error
from .types import (
    BulkReadResult,
    BulkWriteResult,
    FileInfo,
    FilesystemEvent,
    FilesystemEventType,
    JsonObject,
    JsonValue,
    ListResult,
    TransferCancelSignal,
    TransferProgressCallback,
)

WATCH_STOP_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
_FILE_TRANSFER_CHUNK_BYTES: Final[int] = 64 * 1024
_T = TypeVar("_T")
_TRANSFER_CANCEL_POLL_SECONDS: Final[float] = 0.05
_UPLOAD_BOUNDARY_PREFIX: Final[str] = "daytona-upload-"


class _LineStream(Protocol):
    def iter_lines(self) -> AsyncIterator[str]: ...

    async def close(self) -> None: ...


_WatchCallback = Callable[[FilesystemEvent], "None | Awaitable[None]"]
_OverflowCallback = Callable[[], "None | Awaitable[None]"]
_ExitCallback = Callable[[Exception | None], "None | Awaitable[None]"]


async def _invoke(result: None | Awaitable[None]) -> None:
    if inspect.isawaitable(result):
        await result


def _watch_spec(path: str, *, recursive: bool) -> RequestSpec:
    params: Dict[str, JsonValue] = {"path": path}
    if recursive:
        params["recursive"] = True
    return RequestSpec(
        "GET",
        "/files/watch",
        params=params,
        timeout=None,
        unbounded_read=True,
        headers={"Accept": "text/event-stream"},
    )


def _parse_filesystem_event(payload: JsonObject) -> FilesystemEvent:
    try:
        event_type = FilesystemEventType(str(payload.get("type")))
    except ValueError as exc:
        raise create_error(f"invalid filesystem event type: {payload.get('type')}", 500, None, "SDK") from exc
    name = payload.get("name")
    is_dir = payload.get("isDir")
    timestamp = payload.get("timestamp")
    if not isinstance(name, str):
        raise create_error("filesystem watch event name must be a string", 500, None, "SDK")
    if not isinstance(is_dir, bool):
        raise create_error("filesystem watch event isDir must be a boolean", 500, None, "SDK")
    if not isinstance(timestamp, int):
        raise create_error("filesystem watch event timestamp must be an integer", 500, None, "SDK")
    return FilesystemEvent(type=event_type, name=name, is_dir=is_dir, timestamp=timestamp)


class AsyncWatchHandle:
    """A running directory watch.

    Single-task, single-loop: the reader is one ``asyncio.Task`` and every
    method runs on the same loop, so the sync version's five locks collapse to
    plain attributes. Only the exit event survives, because ``wait_for_exit``
    genuinely waits.
    """

    def __init__(
        self,
        stream: _LineStream,
        events: asyncio.Queue[FilesystemEvent],
        on_exit: Optional[_ExitCallback] = None,
    ) -> None:
        self._stream = stream
        self._reader: asyncio.Task[None] | None = None
        self._events = events
        self._on_exit = on_exit
        self._stopped = False
        self._delivering = False
        self._close_error: Exception | None = None
        self._exited = asyncio.Event()
        self._overflow_count = 0
        self._exit_error: Exception | None = None
        self._exit_notified = False
        self._last_callback_error: Exception | None = None

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    def attach_reader(self, reader: asyncio.Task[None]) -> None:
        self._reader = reader

    @property
    def exit_error(self) -> Exception | None:
        return self._exit_error

    @property
    def last_callback_error(self) -> Exception | None:
        return self._last_callback_error

    def record_callback_error(self, error: Exception) -> None:
        self._last_callback_error = error

    async def record_exit(self, error: Exception | None) -> None:
        if self._exit_notified:
            return
        self._exit_notified = True
        # A stream that failed to close on stop() is an exit error too; an
        # earlier reader error still wins, matching the reader's own policy.
        self._exit_error = error or self._close_error
        self._exited.set()
        if self._on_exit is None:
            return
        try:
            await _invoke(self._on_exit(self._exit_error))
        except Exception as exc:
            self.record_callback_error(exc)

    def record_overflow(self) -> None:
        self._overflow_count += 1

    async def wait_for_exit(self) -> Exception | None:
        reader = self._reader
        if reader is not None and reader is asyncio.current_task():
            return self.exit_error
        await self._exited.wait()
        return self.exit_error

    def get_new_events(self) -> list[FilesystemEvent]:
        items: list[FilesystemEvent] = []
        while True:
            try:
                items.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                return items

    async def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            # Best effort, like the sync version: a stream that fails to close
            # must not stop us from waiting for the reader and reporting exit —
            # but the failure is kept so record_exit() can report it.
            try:
                await self._stream.close()
            except Exception as close_error:
                self._close_error = close_error
        reader = self._reader
        if reader is None:
            await self.record_exit(None)
            return
        if reader is asyncio.current_task():
            return
        # Closing the stream makes the reader's iteration end; wait for it to
        # observe that and run its own exit path. Wait on the exit EVENT, not
        # the task: awaiting the task would re-raise a failed reader's own
        # error out of stop(), which the sync version's join() never did —
        # that failure is recorded into exit_error and belongs to
        # wait_for_exit(). The bound guards against a reader stuck in stream
        # I/O; a callback still running is waited for in full, as the sync
        # version's delivery lock did, so no delivery is in flight when stop()
        # returns.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._exited.wait(), timeout=WATCH_STOP_JOIN_TIMEOUT_SECONDS)
        while self._delivering and not reader.done():
            await asyncio.sleep(0)


# Server ceiling on a pre-signed file URL's lifetime (7 days). The API rejects
# an ``expires`` further ahead than this, so the SDK refuses it before the
# request; never-expiring links do not exist.
SIGNED_FILE_MAX_TTL_SECONDS = 604_800

# ``(method, path, expires) -> response body`` of the API's
# ``POST …/files/sign``: ``method`` is ``GET`` for a download link and ``POST``
# for an upload link, ``expires`` an absolute unix time in seconds.
FileUrlSigner = Callable[[str, str, int], Awaitable[TransportPayload]]


class AsyncFileSystemService:
    """Sandbox filesystem namespace."""

    def __init__(
        self,
        transport: AsyncTransport,
        *,
        file_url_signer: Optional[FileUrlSigner] = None,
    ) -> None:
        self._transport = transport
        self._file_url_signer = file_url_signer

    async def read_bytes(self, path: str, *, max_response_bytes: Optional[int] = None) -> bytes:
        """Read a file as bytes.

        The whole file is held in memory, with no total deadline (it fails
        only when no bytes move for the client's ``request_timeout``).
        ``max_response_bytes`` replaces the client's cap on the body for this
        call (``0`` = no limit); a larger body raises ``PayloadTooLargeError``
        before it is read in full. For large files, or content from a sandbox
        you don't trust, use :meth:`download_file`, which streams to disk.
        """
        spec = file_download_spec(path, timeout=None)
        if max_response_bytes is not None:
            spec = replace(
                spec,
                max_response_bytes=validated_max_response_bytes("max_response_bytes", max_response_bytes),
            )
        return await self._transport.raw(spec)

    async def read_text(self, path: str, *, max_response_bytes: Optional[int] = None) -> str:
        """Read a UTF-8 text file (see :meth:`read_bytes` for its bounds)."""
        return (await self.read_bytes(path, max_response_bytes=max_response_bytes)).decode("utf-8")

    async def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> None:
        """Write bytes to a file, optionally via temp-file + rename."""
        await self._transport.send(file_upload_spec(path, content, timeout=None, atomic=atomic))

    async def write_text(self, path: str, content: str, *, atomic: bool = False) -> None:
        """Write a UTF-8 text file, optionally via temp-file + rename."""
        await self.write_bytes(path, content.encode("utf-8"), atomic=atomic)

    async def presigned_download_url(self, path: str, *, expires_in_seconds: int = 3600) -> str:
        """Return a pre-signed download URL for a sandbox file.

        The signature is minted by the API; ``expires_in_seconds``
        must be between 1 and :data:`SIGNED_FILE_MAX_TTL_SECONDS` (7 days).
        """
        return await self._presigned_url("GET", "/files/download", path, expires_in_seconds)

    async def presigned_upload_url(self, path: str, *, expires_in_seconds: int = 3600) -> str:
        """Return a pre-signed upload URL for a sandbox file.

        The signature is minted by the API; ``expires_in_seconds``
        must be between 1 and :data:`SIGNED_FILE_MAX_TTL_SECONDS` (7 days).
        """
        return await self._presigned_url("POST", "/files/upload", path, expires_in_seconds)

    async def write_many(self, files: Mapping[str, bytes], *, atomic: bool = False) -> BulkWriteResult:
        """Write several files in one request, optionally atomically per file."""
        spec = file_bulk_upload_spec(files, timeout=None, atomic=atomic)
        payload = await self._transport.send(spec)
        if isinstance(payload, bytes):
            raise create_error("expected JSON object", 400, "BAD_REQUEST", "SDK")
        return parse_bulk_write_result(payload)

    async def upload_file(
        self,
        local_path: str | os.PathLike[str],
        remote_path: str,
        *,
        on_progress: TransferProgressCallback | None = None,
        cancel: TransferCancelSignal | None = None,
        atomic: bool = False,
    ) -> None:
        """Stream a local file into the sandbox without buffering it whole in memory."""
        _raise_if_transfer_cancelled(cancel, operation="upload")
        source_path = Path(local_path)
        total_bytes = source_path.stat().st_size
        filename = source_path.name or "file"
        boundary = _multipart_boundary()
        preamble = _multipart_preamble(boundary, filename)
        closing = _multipart_closing_boundary(boundary)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(preamble) + total_bytes + len(closing)),
        }
        retry_progress = _RetryProgress(on_progress)
        # The body generator observes `cancel` while it streams; once it is
        # exhausted the request is still in flight (server-side processing),
        # so the send itself is raced against the signal too.
        await _await_cancellable(
            self._transport.request(
                "POST",
                "/files/upload",
                transfer=True,
                params=_atomic_params(path=remote_path, atomic=atomic),
                headers=headers,
                content_factory=lambda attempt: _upload_multipart_content(
                    source_path,
                    preamble=preamble,
                    closing=closing,
                    total_bytes=total_bytes,
                    on_progress=retry_progress.callback_for_attempt(attempt),
                    cancel=cancel,
                ),
            ),
            cancel=cancel,
            operation="upload",
        )

    async def download_file(
        self,
        remote_path: str,
        local_path: str | os.PathLike[str],
        *,
        on_progress: TransferProgressCallback | None = None,
        cancel: TransferCancelSignal | None = None,
    ) -> None:
        destination_path = Path(local_path)
        bytes_written = 0
        progress_calls = 0
        temporary_path: Path | None = None
        _raise_if_transfer_cancelled(cancel, operation="download")
        stream = await _await_cancellable(
            self._transport.open_stream(file_download_spec(remote_path, timeout=None)),
            cancel=cancel,
            operation="download",
        )
        total_bytes = _content_length(stream.headers)
        try:
            temporary_path, handle = _open_download_staging_file(destination_path)
            with handle:
                iterator = stream.iter_bytes().__aiter__()
                while True:
                    try:
                        chunk = await _read_download_chunk(iterator, cancel=cancel)
                    except StopAsyncIteration:
                        break
                    _raise_if_transfer_cancelled(cancel, operation="download")
                    await asyncio.to_thread(handle.write, chunk)
                    bytes_written += len(chunk)
                    if on_progress is not None:
                        _report_transfer_progress(on_progress, bytes_written, total_bytes)
                        progress_calls += 1
                _raise_if_length_mismatch(bytes_written, total_bytes)
                _raise_if_transfer_cancelled(cancel, operation="download")
                await asyncio.to_thread(_preserve_replaced_file_mode, handle.fileno(), destination_path)
            if (
                on_progress is not None
                and total_bytes is not None
                and (progress_calls == 0 or bytes_written != total_bytes)
            ):
                _report_transfer_progress(on_progress, bytes_written, total_bytes)
            assert temporary_path is not None
            await asyncio.to_thread(os.replace, temporary_path, destination_path)
        except TransferCancelledError:
            if temporary_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    temporary_path.unlink()
            await stream.close()
            raise
        except BaseException:
            if temporary_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    temporary_path.unlink()
            raise
        finally:
            await stream.close()

    async def read_many(
        self, paths: Sequence[str], *, max_response_bytes: Optional[int] = None
    ) -> BulkReadResult:
        """Read multiple files, buffering each returned file in memory before returning.

        Non-200 responses raise the typed SDK error inside the transport;
        only a 200 multipart body reaches the parser. The limits are those of
        :meth:`read_bytes`; ``max_response_bytes`` applies to the whole
        multipart body.
        """
        limit = (
            None
            if max_response_bytes is None
            else validated_max_response_bytes("max_response_bytes", max_response_bytes)
        )
        spec = bulk_download_spec(paths, timeout=None)
        request_kwargs = {"json": spec.json_body}
        if spec.timeout is not None:
            request_kwargs["timeout"] = spec.timeout
        if limit is None:
            response = await self._transport.request(spec.method, spec.path, transfer=True, **request_kwargs)
        else:
            response = await self._transport.request(
                spec.method, spec.path, transfer=True, max_response_bytes=limit, **request_kwargs
            )
        return parse_bulk_read_result(response.content, content_type=response.headers.get("content-type"))

    async def list(self, path: str, *, depth: Optional[int] = None) -> ListResult[FileInfo]:
        """List directory entries as typed ``FileInfo`` items (``items``, ``next_cursor``)."""
        params: Dict[str, JsonValue] = {"path": path}
        if depth is not None:
            params["depth"] = depth
        raw_items, next_cursor = list_shape(
            await self._transport.send(RequestSpec("GET", "/files", params=params))
        )
        items = [expect_file_info(item, parent=path) for item in raw_items]
        return ListResult(items=items, next_cursor=next_cursor)

    async def stat(self, path: str) -> FileInfo:
        """Return file metadata."""
        return expect_file_info(
            await self._transport.send(RequestSpec("GET", "/files/info", params={"path": path}))
        )

    async def mkdir(self, path: str, *, mode: str = "755") -> None:
        """Create a directory."""
        await self._transport.send(RequestSpec("POST", "/files/folder", params={"path": path, "mode": mode}))

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        """Remove a file, or a directory with ``recursive=True``."""
        await self._transport.send(
            RequestSpec("DELETE", "/files", params={"path": path, "recursive": recursive})
        )

    async def move(self, source: str, destination: str) -> None:
        """Move or rename files."""
        await self._transport.send(
            RequestSpec("POST", "/files/move", params={"source": source, "destination": destination})
        )

    async def grep(self, path: str, pattern: str) -> ListResult:
        """Search file contents under ``path`` for the literal substring ``pattern``.

        Returns matches with one-based line numbers.
        """
        items, next_cursor = list_shape(
            await self._transport.send(
                RequestSpec("GET", "/files/find", params={"path": path, "pattern": pattern})
            )
        )
        return ListResult(items=items, next_cursor=next_cursor)

    async def glob(self, path: str, pattern: str) -> ListResult[str]:
        """Return the paths of files under ``path`` whose base name matches the glob ``pattern``."""
        payload = await self._transport.send(
            RequestSpec("GET", "/files/search", params={"path": path, "pattern": pattern})
        )
        if isinstance(payload, list):
            return ListResult([str(item) for item in payload], None)
        obj = expect_object(payload)
        raw = obj.get("items") if isinstance(obj.get("items"), list) else obj.get("files")
        items = [str(item) for item in raw] if isinstance(raw, list) else []
        next_cursor = obj.get("nextCursor") or obj.get("next_cursor")
        return ListResult(items, str(next_cursor) if isinstance(next_cursor, str) else None)

    async def replace(self, files: Sequence[str], pattern: str, new_value: str) -> ListResult:
        """Replace text in files."""
        items, next_cursor = list_shape(
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/files/replace",
                    json_body={"files": list(files), "pattern": pattern, "newValue": new_value},
                )
            )
        )
        return ListResult(items=items, next_cursor=next_cursor)

    async def chmod(
        self,
        path: str,
        *,
        mode: Optional[str] = None,
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> None:
        """Set file permissions and ownership."""
        params: Dict[str, JsonValue] = {"path": path}
        if mode is not None:
            params["mode"] = mode
        if owner is not None:
            params["owner"] = owner
        if group is not None:
            params["group"] = group
        await self._transport.send(RequestSpec("POST", "/files/permissions", params=params))

    async def watch_dir(
        self,
        path: str,
        on_event: Optional[_WatchCallback] = None,
        *,
        recursive: bool = False,
        on_overflow: Optional[_OverflowCallback] = None,
        on_exit: Optional[_ExitCallback] = None,
    ) -> AsyncWatchHandle:
        stream = await self._transport.open_stream(_watch_spec(path, recursive=recursive))
        events: asyncio.Queue[FilesystemEvent] = asyncio.Queue()
        handle = AsyncWatchHandle(stream, events, on_exit=on_exit)
        ready = asyncio.Event()
        reader_error: list[Exception] = []

        async def read_events() -> None:
            started = False
            exit_error: Exception | None = None
            try:
                async for event_name, payload in parse_sse_lines_async(stream.iter_lines()):
                    if handle._stopped:
                        break
                    if event_name == "start":
                        started = True
                        ready.set()
                        continue
                    if event_name == "fs":
                        event = _parse_filesystem_event(payload)
                        if handle._stopped:
                            break
                        events.put_nowait(event)
                        if on_event is not None:
                            handle._delivering = True
                            try:
                                await _invoke(on_event(event))
                            except Exception as exc:
                                handle.record_callback_error(exc)
                            finally:
                                handle._delivering = False
                        continue
                    if event_name == "overflow":
                        if handle._stopped:
                            break
                        handle.record_overflow()
                        if on_overflow is not None:
                            handle._delivering = True
                            try:
                                await _invoke(on_overflow())
                            except Exception as exc:
                                handle.record_callback_error(exc)
                            finally:
                                handle._delivering = False
                        continue
                if started:
                    if not handle._stopped:
                        exit_error = create_error(
                            "filesystem watch stream ended unexpectedly", 500, None, "SDK"
                        )
                else:
                    exit_error = create_error("filesystem watch stream ended before start", 500, None, "SDK")
                    reader_error.append(exit_error)
                    ready.set()
            except Exception as exc:
                if not started:
                    # watch_dir() raises this; on_exit / exit_error must see it too,
                    # matching the TypeScript handle's pre-start failure path.
                    exit_error = exc
                    reader_error.append(exc)
                    ready.set()
                elif not handle._stopped:
                    exit_error = exc
            finally:
                # record_exit must run whatever close() does: it is what wakes
                # wait_for_exit()/stop() and fires on_exit. A close() failure is
                # the exit error if nothing earlier already was.
                try:
                    await stream.close()
                except Exception as close_error:
                    exit_error = exit_error or close_error
                await handle.record_exit(exit_error)

        reader = asyncio.ensure_future(read_events())
        handle.attach_reader(reader)
        try:
            await ready.wait()
        except BaseException:
            # Cancelled (or torn down) before the start frame: the detached
            # reader would otherwise keep the HTTP stream open indefinitely.
            await handle.stop()
            raise
        if reader_error:
            raise reader_error[0]
        return handle

    async def _presigned_url(self, method: str, route: str, path: str, expires_in_seconds: int) -> str:
        # Validate before any network: the server would answer 400 for an
        # out-of-range ``expires`` anyway, but a bad argument is the caller's
        # bug and should not cost a round trip or depend on clock skew.
        expires = _expires_unix_seconds(expires_in_seconds)
        signed = _signed_file_url_from_payload(await self._require_file_url_signer()(method, path, expires))
        # The URL is composed from the handle's OWN toolbox base, not the
        # server's ``url`` field: a client configured with a direct proxy
        # ``toolbox_url`` keeps receiving links on that base. The signature
        # covers the server's ``expires``, so that value is echoed, not ours.
        return (
            f"{self._transport.base_url}{route}"
            f"?path={quote(path, safe='')}&expires={signed.expires}&signature={signed.signature}"
        )

    def _require_file_url_signer(self) -> FileUrlSigner:
        if self._file_url_signer is None:
            raise create_error("filesystem handle has no pre-signed URL signer", 400, "BAD_REQUEST", "SDK")
        return self._file_url_signer


def _expires_unix_seconds(expires_in_seconds: int) -> int:
    _assert_expires_in_seconds(expires_in_seconds)
    return int(time.time()) + expires_in_seconds


def _assert_expires_in_seconds(expires_in_seconds: int) -> None:
    if (
        isinstance(expires_in_seconds, bool)
        or not isinstance(expires_in_seconds, int)
        or not 1 <= expires_in_seconds <= SIGNED_FILE_MAX_TTL_SECONDS
    ):
        raise create_error(
            f"expires_in_seconds must be an integer between 1 and {SIGNED_FILE_MAX_TTL_SECONDS} (7 days)",
            400,
            "BAD_REQUEST",
            "SDK",
        )


class _SignedFileUrl(NamedTuple):
    signature: str
    expires: int


def _signed_file_url_from_payload(payload: TransportPayload) -> _SignedFileUrl:
    # Takes the raw decoded body, whatever JSON type it is: a ``null``, a list,
    # a string or a malformed object all answer the same invalid-payload error
    # (parity with the TS SDK's ``readSignedFileUrl``).
    if not isinstance(payload, dict):
        raise create_error("API returned an invalid signed file URL payload", 500, None, "SDK")
    signature = payload.get("signature")
    expires_at = payload.get("expires_at")
    if isinstance(signature, str) and signature and isinstance(expires_at, str):
        try:
            parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            parsed_expiry = None
        if parsed_expiry is not None and parsed_expiry.tzinfo is not None:
            return _SignedFileUrl(signature=signature, expires=int(parsed_expiry.timestamp()))
    raise create_error("API returned an invalid signed file URL payload", 500, None, "SDK")


def _multipart_boundary() -> str:
    return f"{_UPLOAD_BOUNDARY_PREFIX}{secrets.token_hex(16)}"


def _multipart_preamble(boundary: str, filename: str) -> bytes:
    escaped = _multipart_filename(filename)
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{escaped}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()


def _multipart_filename(filename: str) -> str:
    return filename.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _multipart_closing_boundary(boundary: str) -> bytes:
    return f"\r\n--{boundary}--\r\n".encode()


def _atomic_params(*, path: str, atomic: bool) -> Dict[str, JsonValue]:
    params: Dict[str, JsonValue] = {"path": path}
    if atomic:
        params["atomic"] = True
    return params


async def _upload_multipart_content(
    source_path: Path,
    *,
    preamble: bytes,
    closing: bytes,
    total_bytes: int,
    on_progress: TransferProgressCallback | None,
    cancel: TransferCancelSignal | None,
) -> AsyncIterator[bytes]:
    yield preamble
    sent = 0
    progress_calls = 0
    with source_path.open("rb") as handle:
        while sent < total_bytes:
            _raise_if_transfer_cancelled(cancel, operation="upload")
            remaining = total_bytes - sent
            chunk = await asyncio.to_thread(handle.read, min(_FILE_TRANSFER_CHUNK_BYTES, remaining))
            if not chunk:
                raise create_error(
                    "source file changed size during upload",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                )
            sent += len(chunk)
            if on_progress is not None:
                on_progress(sent, total_bytes)
                progress_calls += 1
            yield chunk
    # Final progress report (empty files, or a source that shrank) runs BEFORE
    # the closing boundary so a callback that cancels can still stop the request.
    if on_progress is not None and (progress_calls == 0 or sent != total_bytes):
        on_progress(sent, total_bytes)
    _raise_if_transfer_cancelled(cancel, operation="upload")
    yield closing


class _RetryProgress:
    def __init__(self, on_progress: TransferProgressCallback | None) -> None:
        self._on_progress = on_progress
        self._furthest_sent = 0

    def callback_for_attempt(self, attempt: int) -> TransferProgressCallback | None:
        if self._on_progress is None:
            return None
        on_progress = self._on_progress

        def callback(sent: int, total: int | None) -> None:
            if attempt > 0 and sent <= self._furthest_sent:
                return
            self._furthest_sent = max(self._furthest_sent, sent)
            _report_transfer_progress(on_progress, sent, total)

        return callback


def _report_transfer_progress(
    on_progress: TransferProgressCallback,
    sent: int,
    total: int | None,
) -> None:
    try:
        on_progress(sent, total)
    except Exception:
        return


async def _await_cancellable(
    awaitable: Awaitable[_T],
    *,
    cancel: TransferCancelSignal | None,
    operation: str,
) -> _T:
    """Await `awaitable`, raising TransferCancelledError (and cancelling the
    underlying task) as soon as `cancel` is set, even if the awaitable is
    stalled on the network."""
    if cancel is None:
        return await awaitable
    task: asyncio.Task[_T] = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=_TRANSFER_CANCEL_POLL_SECONDS)
            if done:
                return task.result()
            _raise_if_transfer_cancelled(cancel, operation=operation)
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        raise


async def _read_download_chunk(
    iterator: AsyncIterator[bytes],
    *,
    cancel: TransferCancelSignal | None,
) -> bytes:
    read_task: asyncio.Task[bytes] = asyncio.create_task(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({read_task}, timeout=_TRANSFER_CANCEL_POLL_SECONDS)
            if done:
                return read_task.result()
            _raise_if_transfer_cancelled(cancel, operation="download")
    except BaseException:
        if not read_task.done():
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read_task
        raise


def _open_download_staging_file(destination: Path) -> tuple[Path, BinaryIO]:
    """Exclusively create a random `.daytona-dl-*` sibling of `destination`.

    `O_CREAT | O_EXCL` never follows a planted symlink; mode `0o600` keeps the
    staging file private to the owner while streaming — the intended final mode
    (destination's mode or `0o666 & ~umask`) is applied via ``_preserve_replaced_file_mode``
    immediately before ``os.replace``.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(16):
        candidate = destination.parent / f".daytona-dl-{secrets.token_hex(8)}"
        try:
            fd = os.open(candidate, flags, 0o600)
        except FileExistsError:
            continue
        return candidate, os.fdopen(fd, "wb")
    raise FileExistsError(f"could not allocate a staging file next to {destination}")


def _raise_if_length_mismatch(bytes_written: int, total_bytes: int | None) -> None:
    if total_bytes is not None and bytes_written != total_bytes:
        # A truncated transfer, as when the socket drops mid-body.
        raise ConnectionError("download_file() ended before the declared Content-Length was received")


def _preserve_replaced_file_mode(staging_fd: int, destination: Path) -> None:
    """Apply the intended final mode to the staging file before replacement.

    For an existing regular-file destination: copies its permission bits.
    For a new destination: applies ``0o666 & ~umask`` (ordinary creation mode).
    On platforms without ``os.fchmod`` (Windows) the call is skipped.
    """
    if not hasattr(os, "fchmod"):
        return
    try:
        destination_stat = destination.stat(follow_symlinks=False)
        if not stat.S_ISREG(destination_stat.st_mode):
            return
        mode = stat.S_IMODE(destination_stat.st_mode) & 0o777
    except FileNotFoundError:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    os.fchmod(staging_fd, mode)


def _raise_if_transfer_cancelled(cancel: TransferCancelSignal | None, *, operation: str) -> None:
    if cancel is not None and cancel.is_set():
        raise TransferCancelledError(
            f"{operation} transfer cancelled", status_code=499, code="REQUEST_CANCELLED", source="SDK"
        )


def _content_length(headers: Mapping[str, str]) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None:
        raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
