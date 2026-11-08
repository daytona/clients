# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import os
import secrets
import stat
import time
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

from ._bridge import BridgeError
from ._core import (
    RequestSpec,
    TransportPayload,
    bulk_download_spec,
    expect_file_info,
    file_bulk_upload_spec,
    file_download_spec,
    file_upload_spec,
    list_shape,
    parse_bulk_read_result,
    parse_bulk_write_result,
    parse_file_match,
    parse_replace_result,
    parse_sse_messages_async,
    sse_json,
)
from ._timestamps import parse_timestamp
from ._transport import AsyncTransport
from .errors import ConnectionError, TransferCancelledError, create_error
from .types import (
    BulkReadResult,
    BulkWriteResult,
    FileInfo,
    FileMatch,
    FilesystemEvent,
    JsonObject,
    JsonValue,
    ListResult,
    ReplaceResult,
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


WatchEventCallback = Callable[[FilesystemEvent], "None | Awaitable[None]"]
"""``watch_dir()`` event callback; may be a coroutine function."""
WatchOverflowCallback = Callable[[], "None | Awaitable[None]"]
"""``watch_dir()`` overflow callback: the sandbox dropped events; may be a coroutine function."""
WatchExitCallback = Callable[[Optional[Exception]], "None | Awaitable[None]"]
"""``watch_dir()`` exit callback: ``None`` after ``stop()``, else the error that ended the watch."""


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
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        raise create_error(
            "expected filesystem watch event: type must be a string", 400, "BAD_REQUEST", "SDK"
        )
    name = payload.get("name")
    is_dir = payload.get("isDir")
    timestamp = payload.get("timestamp")
    if not isinstance(name, str):
        raise create_error(
            "expected filesystem watch event: name must be a string", 400, "BAD_REQUEST", "SDK"
        )
    if not isinstance(is_dir, bool):
        raise create_error(
            "expected filesystem watch event: isDir must be a boolean", 400, "BAD_REQUEST", "SDK"
        )
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise create_error(
            "expected filesystem watch event: timestamp must be an integer", 400, "BAD_REQUEST", "SDK"
        )
    return FilesystemEvent(type=event_type, name=name, is_dir=is_dir, timestamp_ms=timestamp)


# The delivery a watch callback (or ``on_exit``) runs in: set for exactly
# that call and inherited by the tasks it starts, so a ``stop()`` /
# ``wait_for_exit()`` the callback makes, directly or from a child task, is
# told apart from an external one.
_WATCH_DELIVERY: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "_WATCH_DELIVERY", default=None
)


class AsyncWatchHandle:
    """A running directory watch, returned by ``fs.watch_dir()``.

    Callbacks run one at a time, in order, and are awaited. A failing event or
    overflow callback ends the watch: that error becomes :attr:`exit_error`
    and is passed to ``on_exit``. Never constructed directly.

    With the synchronous client, callbacks run on the SDK's event-loop thread:
    there a callback may call its own watch's ``stop()`` (which then returns
    without waiting), ``wait_for_exit()`` and ``exit_error`` (which return the
    current exit error), but no other synchronous SDK call (another watch's
    included): that raises ``BridgeError``.
    """

    # Called on the bridge loop thread (from a callback the SDK runs), the
    # synchronous twin's stop() / wait_for_exit() use these non-blocking
    # methods instead of blocking on the loop they run on.
    _SYNC_LOOP_VARIANTS: Mapping[str, str] = {"stop": "_stop_nowait", "wait_for_exit": "_exit_error_now"}

    def __init__(self, _stream: _LineStream, _on_exit: Optional[WatchExitCallback] = None) -> None:
        self._stream = _stream
        self._reader: asyncio.Task[None] | None = None
        self._on_exit = _on_exit
        self._stopped = False
        self._closing = False
        self._stop_task: asyncio.Task[None] | None = None
        # The token of the callback (or on_exit) call in progress; None between calls.
        self._active_delivery: object | None = None
        self._exited = asyncio.Event()
        # Set once on_exit has returned: an external stop() returns after it.
        self._exit_done = asyncio.Event()
        self._exit_error: Exception | None = None
        self._exit_notified = False

    @property
    def exit_error(self) -> Exception | None:
        """Why the watch ended: ``None`` while running or after ``stop()``."""
        return self._exit_error

    async def _record_exit(self, error: Exception | None, *, notify: bool = True) -> None:
        if self._exit_notified:
            return
        self._exit_notified = True
        self._exit_error = error
        self._exited.set()
        try:
            if notify and self._on_exit is not None:
                on_exit = self._on_exit
                # An on_exit failure is contained: the watch has already ended.
                with contextlib.suppress(Exception):
                    await self._deliver(lambda: on_exit(self._exit_error))
        finally:
            self._exit_done.set()

    async def _deliver(self, callback: Callable[[], None | Awaitable[None]]) -> None:
        """Run one callback as a delivery of this watch (see ``_WATCH_DELIVERY``)."""
        token = object()
        self._active_delivery = token
        reset = _WATCH_DELIVERY.set(token)
        try:
            await _invoke(callback())
        finally:
            _WATCH_DELIVERY.reset(reset)
            self._active_delivery = None

    def _own_call(self) -> bool:
        """Whether the caller is this watch's running callback (or a task it started)."""
        return self._active_delivery is not None and _WATCH_DELIVERY.get() is self._active_delivery

    async def wait_for_exit(self) -> Exception | None:
        """Wait until the watch ends and return :attr:`exit_error` (its own callback: at once).

        Called by the watch's own callback, it returns the current exit error
        without waiting for itself.
        """
        if self._own_call():
            return self.exit_error
        await self._exited.wait()
        return self.exit_error

    async def stop(self) -> None:
        """Stop the watch, once a running callback and ``on_exit`` finish (its own callback: at once).

        A callback (or ``on_exit``) may call ``stop()`` on its own watch, also
        from a task it started; that call returns without waiting for itself.
        """
        own = self._own_call()
        self._stopped = True
        if not self._closing:
            self._closing = True
            # A stream that fails to close must not stop us from waiting for
            # the reader; after stop() the watch ends without an error.
            with contextlib.suppress(Exception):
                await self._stream.close()
        reader = self._reader
        if reader is None:
            await self._record_exit(None)
            return
        if own or reader is asyncio.current_task():
            return
        # Closing the stream ends the reader's iteration. Wait on the exit
        # EVENTS, not the task, so a reader failure is reported through
        # exit_error rather than raised here; the bound guards against a
        # reader stuck in stream I/O. A callback still running, and on_exit,
        # are waited for in full, so neither is in flight when stop() returns.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._exited.wait(), timeout=WATCH_STOP_JOIN_TIMEOUT_SECONDS)
        if self._exited.is_set() or self._active_delivery is not None:
            await self._exit_done.wait()

    def _refuse_other_watch(self, method: str) -> None:
        if not self._own_call():
            raise BridgeError(
                f"a synchronous watch callback runs on the SDK's event-loop thread, where only its own "
                f"watch's stop() / wait_for_exit() work; {method}() on another watch cannot block there. "
                "Use the async client (AsyncDaytona) to coordinate several watches."
            )

    def _stop_nowait(self) -> None:
        """Stop without waiting: no event is delivered after this returns, and
        the stream closes in the background (see ``_SYNC_LOOP_VARIANTS``)."""
        self._refuse_other_watch("stop")
        self._stopped = True
        if self._stop_task is None:
            self._stop_task = asyncio.ensure_future(self.stop())

    def _exit_error_now(self) -> Exception | None:
        self._refuse_other_watch("wait_for_exit")
        return self._exit_error


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
        _transport: AsyncTransport,
        *,
        _file_url_signer: Optional[FileUrlSigner] = None,
    ) -> None:
        self._transport = _transport
        self._file_url_signer = _file_url_signer

    async def read_bytes(self, path: str) -> bytes:
        """Read a file as bytes."""
        value = await self._transport.raw(file_download_spec(path, timeout=None))
        return value

    async def read_text(self, path: str) -> str:
        """Read a UTF-8 text file.

        Decoding is strict (invalid UTF-8 raises ``UnicodeDecodeError``) and a
        leading byte-order mark is kept as ``\\ufeff``.
        """
        return (await self.read_bytes(path)).decode("utf-8")

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
        """Stream ``remote_path`` to ``local_path`` without holding it in memory.

        Writes to a unique temporary file in the destination directory and
        renames it over ``local_path`` on success; on an error or a
        cancellation that temporary file is removed. ``on_progress(bytes,
        total)`` reports progress (``total`` from ``Content-Length`` when the
        sandbox sends it); ``cancel`` is any object with ``is_set()``, checked
        between chunks, and raises ``TransferCancelledError``.
        """
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

    async def read_many(self, paths: Sequence[str]) -> BulkReadResult:
        """Read multiple files, buffering each returned file in memory before returning.

        Non-200 responses raise the typed SDK error inside the transport;
        only a 200 multipart body reaches the parser.
        """
        spec = bulk_download_spec(paths, timeout=None)
        request_kwargs = {"json": spec.json_body}
        if spec.timeout is not None:
            request_kwargs["timeout"] = spec.timeout
        response = await self._transport.request(spec.method, spec.path, transfer=True, **request_kwargs)
        return parse_bulk_read_result(response.content, content_type=response.headers.get("content-type"))

    async def list(self, path: str, *, depth: Optional[int] = None) -> ListResult[FileInfo]:
        """List directory entries as typed ``FileInfo`` items (``items``, ``next_cursor``)."""
        params: Dict[str, JsonValue] = {"path": path}
        if depth is not None:
            params["depth"] = depth
        raw_items, next_cursor = list_shape(
            await self._transport.send(RequestSpec("GET", "/files", params=params)),
            description="file list response",
        )
        items = [expect_file_info(item) for item in raw_items]
        return ListResult(items=items, next_cursor=next_cursor)

    async def stat(self, path: str) -> FileInfo:
        """Return file metadata."""
        return expect_file_info(
            await self._transport.send(RequestSpec("GET", "/files/info", params={"path": path}))
        )

    async def mkdir(self, path: str, *, mode: str = "0755") -> None:
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

    async def grep(self, path: str, pattern: str) -> ListResult[FileMatch]:
        """Search file contents under ``path`` for the literal substring ``pattern``.

        Returns matches with one-based line numbers.
        """
        items, next_cursor = list_shape(
            await self._transport.send(
                RequestSpec("GET", "/files/find", params={"path": path, "pattern": pattern})
            ),
            description="grep response",
        )
        return ListResult(items=[parse_file_match(item) for item in items], next_cursor=next_cursor)

    async def glob(self, path: str, pattern: str) -> ListResult[str]:
        """Return the paths of files under ``path`` whose base name matches the glob ``pattern``."""
        payload = await self._transport.send(
            RequestSpec("GET", "/files/search", params={"path": path, "pattern": pattern})
        )
        # The contract's SearchResponse: { files } only.
        if not isinstance(payload, dict):
            raise create_error("expected glob response", 400, "BAD_REQUEST", "SDK")
        raw = payload.get("files")
        if not isinstance(raw, list):
            raise create_error("expected glob response: files is required", 400, "BAD_REQUEST", "SDK")
        if not all(isinstance(item, str) for item in raw):
            raise create_error(
                "expected glob response: files must be a list of strings", 400, "BAD_REQUEST", "SDK"
            )
        return ListResult([str(item) for item in raw], None)

    async def replace(self, files: Sequence[str], pattern: str, new_value: str) -> ListResult[ReplaceResult]:
        """Replace the literal substring ``pattern`` with ``new_value`` in ``files``."""
        items, next_cursor = list_shape(
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/files/replace",
                    json_body={"files": list(files), "pattern": pattern, "newValue": new_value},
                )
            ),
            description="replace response",
        )
        return ListResult(items=[parse_replace_result(item) for item in items], next_cursor=next_cursor)

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
        on_event: WatchEventCallback,
        *,
        recursive: bool = False,
        on_overflow: Optional[WatchOverflowCallback] = None,
        on_exit: Optional[WatchExitCallback] = None,
    ) -> AsyncWatchHandle:
        """Watch ``path`` for changes, calling ``on_event`` for each one.

        Returns once the watch is established. Callbacks (plain or coroutine
        functions) run one at a time, in order. A failing ``on_event`` or
        ``on_overflow`` ends the watch with that error; ``on_exit`` runs once
        when the watch ends, and its own failure is ignored. A failure before
        the watch starts is raised from this call and does not call ``on_exit``.
        """
        stream = await self._transport.open_stream(_watch_spec(path, recursive=recursive))
        handle = AsyncWatchHandle(stream, on_exit)
        ready = asyncio.Event()
        reader_error: list[Exception] = []
        callback_error: list[Exception] = []

        async def deliver(callback: Callable[[], None | Awaitable[None]]) -> None:
            try:
                await handle._deliver(callback)
            except Exception as exc:
                callback_error.append(exc)
                raise

        async def read_events() -> None:
            started = False
            exit_error: Exception | None = None
            try:
                async for event_name, data, _id in parse_sse_messages_async(stream.iter_lines()):
                    if handle._stopped:
                        break
                    if event_name == "start":
                        started = True
                        ready.set()
                        continue
                    if event_name == "fs":
                        event = _parse_filesystem_event(sse_json("fs", data, "filesystem watch event"))
                        await deliver(functools.partial(on_event, event))
                        continue
                    if event_name == "overflow" and on_overflow is not None:
                        await deliver(on_overflow)
                        continue
                # A stream that ends on its own is a dropped connection, as a
                # truncated body is (the transport raises ConnectionError).
                if started:
                    if not handle._stopped:
                        exit_error = ConnectionError("filesystem watch stream ended unexpectedly")
                else:
                    # Ended before start: watch_dir() raises it (no handle was
                    # returned), unless watch_dir() itself stopped the handle.
                    if not handle._stopped:
                        reader_error.append(ConnectionError("filesystem watch stream ended before start"))
                    ready.set()
            except Exception as exc:
                if not started:
                    # watch_dir() raises this; the watch never started, so the
                    # exit callback does not run (as in the TypeScript SDK).
                    if not handle._stopped:
                        reader_error.append(exc)
                    ready.set()
                elif not handle._stopped or exc in callback_error:
                    # A failing callback ends the watch with its error, even
                    # when stop() was called while it ran.
                    exit_error = exc
            finally:
                # _record_exit must run whatever close() does: it is what wakes
                # wait_for_exit()/stop() and fires on_exit. A close() failure is
                # the exit error if nothing earlier already was, unless the
                # caller stopped the watch (then it ends without an error).
                try:
                    await stream.close()
                except Exception as close_error:
                    if not handle._stopped:
                        exit_error = exit_error or close_error
                # The exit callback belongs to a watch watch_dir() returned:
                # a failure before start is raised from watch_dir() instead.
                await handle._record_exit(exit_error, notify=started)

        reader = asyncio.ensure_future(read_events())
        handle._reader = reader
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
        raise create_error(
            "expected signed file URL response: signature and expires_at are required",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    signature = payload.get("signature")
    expires_at = payload.get("expires_at")
    if isinstance(signature, str) and signature and isinstance(expires_at, str):
        parsed_expiry = parse_timestamp(expires_at, "signed file URL response: expires_at")
        return _SignedFileUrl(signature=signature, expires=int(parsed_expiry.timestamp()))
    raise create_error(
        "expected signed file URL response: signature and expires_at are required", 400, "BAD_REQUEST", "SDK"
    )


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
