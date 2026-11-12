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
from dataclasses import replace
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

from typing_extensions import Buffer

from ._body_limit import validated_max_response_bytes
from ._bridge import BridgeError, on_a_bridge_loop
from ._core import (
    RequestSpec,
    TransportPayload,
    bulk_download_spec,
    expect_file_info,
    file_bulk_upload_spec,
    file_download_spec,
    file_upload_spec,
    finite_number,
    list_shape,
    parse_bulk_read_result,
    parse_bulk_write_result,
    parse_glob_response,
    parse_grep_response,
    parse_replace_result,
    parse_sse_events_bounded,
    sse_json,
    stream_event_too_large,
)
from ._filesystem_inputs import buffer_bytes, path_sequence, sandbox_path
from ._timestamps import parse_timestamp
from ._transport import AsyncTransport
from .errors import ConnectionError, DaytonaError, TransferCancelledError, create_error
from .types import (
    BulkReadResult,
    BulkWriteResult,
    FileInfo,
    FileMatch,
    FileSearchResult,
    FilesystemEvent,
    JsonObject,
    JsonValue,
    ListResult,
    ReplaceResult,
    TransferCancelSignal,
    TransferProgressCallback,
)

WATCH_STOP_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
# The most fs / overflow events a watch holds before its stream's `start`
# (delivered after it); one more is a malformed stream. Raising it is additive.
WATCH_MAX_EVENTS_BEFORE_START: Final[int] = 4096
# The most bytes a watch's stream may send before `start` (every byte,
# counted as it arrives, so one never-ending event is bounded too): 4096
# events of 256 bytes, about twice a typical event. One byte more is a
# malformed stream, like the count cap.
WATCH_MAX_BYTES_BEFORE_START: Final[int] = 1_048_576
_FILE_TRANSFER_CHUNK_BYTES: Final[int] = 64 * 1024
_T = TypeVar("_T")
_TRANSFER_CANCEL_POLL_SECONDS: Final[float] = 0.05
_UPLOAD_BOUNDARY_PREFIX: Final[str] = "daytona-upload-"


class _ByteStream(Protocol):
    def iter_bytes(self) -> AsyncIterator[bytes]: ...

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


_OVERFLOW_EVENT: Final = "filesystem watch overflow event"


def _check_overflow_event(data: str) -> None:
    """An ``overflow`` event carries ``{"dropped": <boolean>}``; anything else
    is the malformed-response error, whether or not a callback handles it (as
    TypeScript's ``checkOverflowEvent``)."""
    dropped = sse_json("overflow", data, _OVERFLOW_EVENT).get("dropped")
    if not isinstance(dropped, bool):
        problem = "is required" if dropped is None else "must be a boolean"
        raise create_error(f"expected {_OVERFLOW_EVENT}: dropped {problem}", 400, "BAD_REQUEST", "SDK")


def _parse_filesystem_event(payload: JsonObject) -> FilesystemEvent:
    def field(name: str, kind: type[_T], expected: str) -> _T:
        value = payload.get(name)
        # An int a double cannot carry is refused, as JSON.parse reads it as
        # Infinity.
        if (
            not isinstance(value, kind)
            or (kind is int and isinstance(value, bool))
            or (kind is int and not finite_number(value))
        ):
            problem = "is required" if value is None else f"must be {expected}"
            raise create_error(
                f"expected filesystem watch event: {name} {problem}", 400, "BAD_REQUEST", "SDK"
            )
        return value

    return FilesystemEvent(
        type=field("type", str, "a string"),
        name=field("name", str, "a string"),
        is_dir=field("isDir", bool, "a boolean"),
        timestamp_ms=field("timestamp", int, "an integer"),
    )


# The delivery a watch callback (or ``on_exit``) runs in: set for exactly
# that call and inherited by the tasks it starts, so a ``stop()`` /
# ``wait_for_exit()`` the callback makes, directly or from a child task, is
# told apart from an external one.
class _Delivery:
    """One callback delivery's token: it names its watch, so a task the
    callback started is still the watch's own after the callback returned."""

    __slots__ = ("watch",)

    def __init__(self, watch: AsyncWatchHandle) -> None:
        self.watch = watch


_WATCH_DELIVERY: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "_WATCH_DELIVERY", default=None
)


class AsyncWatchHandle:
    """A running directory watch, returned by ``fs.watch_dir()``.

    Callbacks run one at a time, in order, and are awaited. A failing event or
    overflow callback ends the watch: that error becomes :attr:`exit_error`
    and is passed to ``on_exit``. Never constructed directly.

    To stop a watch from an event, signal the code that holds the handle (an
    ``asyncio.Event``, or a ``threading.Event`` with the synchronous client)
    and call ``stop()`` there; this works in every case. As a shorthand, after
    a direct ``await watch_dir(...)`` a callback can call ``stop()`` on the
    returned handle itself: callbacks are held until ``watch_dir()`` has
    returned its handle. When ``watch_dir()`` runs inside another task
    (``asyncio.gather``, ``asyncio.create_task``, or ``asyncio.wait_for`` on
    Python 3.10-3.11), or with the synchronous client, the first events can
    reach the callback before the caller holds the handle.

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

    def __init__(self, _stream: _ByteStream, _on_exit: Optional[WatchExitCallback] = None) -> None:
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
        # Set once watch_dir() has handed the handle to its caller (or the
        # watch is stopped): no callback, on_exit included, runs before.
        self._published = asyncio.Event()

    def _publish(self) -> None:
        """Let callbacks run: watch_dir() has returned this handle (the
        synchronous twin calls it once it has built its own handle)."""
        self._published.set()

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
                await self._published.wait()
                on_exit = self._on_exit
                # An on_exit failure is contained: the watch has already ended.
                with contextlib.suppress(Exception):
                    await self._deliver(lambda: on_exit(self._exit_error))
        finally:
            self._exit_done.set()

    async def _deliver(self, callback: Callable[[], None | Awaitable[None]]) -> None:
        """Run one callback as a delivery of this watch (see ``_WATCH_DELIVERY``)."""
        token = _Delivery(self)
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
        # A reader holding an event for an unpublished handle drops it now.
        self._published.set()
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

    def _owns_caller(self) -> bool:
        """Whether the caller runs in a delivery of this watch, or in a task
        one started, even after that delivery returned (the token travels in
        the task's context)."""
        token = _WATCH_DELIVERY.get()
        return isinstance(token, _Delivery) and token.watch is self

    def _refuse_other_watch(self, method: str) -> None:
        if not self._owns_caller():
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
        self._published.set()
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
    """Sandbox filesystem namespace. All sandbox paths must be absolute strings
    starting with ``/``; relative paths are refused with ``BadRequestError``.
    Local upload/download paths are not subject to this rule.
    """

    # The synchronous watch_dir() publishes the watch once it has built its
    # own handle (see AsyncWatchHandle._publish).
    _SYNC_AFTER_WRAP: Mapping[str, str] = {"watch_dir": "_publish"}

    def __init__(
        self,
        _transport: AsyncTransport,
        *,
        _file_url_signer: Optional[FileUrlSigner] = None,
    ) -> None:
        self._transport = _transport
        self._file_url_signer = _file_url_signer

    async def read_bytes(self, path: str, /, *, max_response_bytes: Optional[int] = None) -> bytes:
        """Read a file as bytes, holding the whole file in memory.

        No total deadline (it fails only when no bytes move for the client's
        ``request_timeout``; with a ``request_timeout`` of ``0``, nothing
        bounds it). ``max_response_bytes`` replaces the client's cap on the
        body for this call (``None`` keeps the client's cap, ``0`` means no
        limit, and any other value (negative, fractional, boolean, string,
        above 2**53 - 1) is a ``BadRequestError``); a larger body raises
        ``PayloadTooLargeError`` before it is read in full. For large files, or
        content from a sandbox you do not trust, use :meth:`download_file`,
        which streams to disk with progress and cancellation.
        """
        spec = file_download_spec(sandbox_path(path), timeout=None)
        if max_response_bytes is not None:
            spec = replace(
                spec,
                max_response_bytes=validated_max_response_bytes("max_response_bytes", max_response_bytes),
            )
        return await self._transport.raw(spec)

    async def read_text(self, path: str, /, *, max_response_bytes: Optional[int] = None) -> str:
        """Read a UTF-8 text file (see :meth:`read_bytes` for its bounds).

        Decoding is strict (invalid UTF-8 raises ``UnicodeDecodeError``) and a
        leading byte-order mark is kept as ``\\ufeff``.
        """
        return (await self.read_bytes(path, max_response_bytes=max_response_bytes)).decode("utf-8")

    async def write_bytes(self, path: str, content: Buffer, /, *, atomic: bool = False) -> None:
        """Write a bytes-like buffer to an absolute path, optionally atomically."""
        spec = file_upload_spec(
            sandbox_path(path), buffer_bytes(content, "content"), timeout=None, atomic=atomic
        )
        await self._transport.send(spec)

    async def write_text(self, path: str, content: str, /, *, atomic: bool = False) -> None:
        """Write a UTF-8 text file, optionally via temp-file + rename."""
        if not isinstance(content, str):
            raise create_error("content must be a string", 400, "BAD_REQUEST", "SDK")
        await self.write_bytes(path, content.encode("utf-8"), atomic=atomic)

    async def presigned_download_url(self, path: str, /, *, expires_in_seconds: int = 3600) -> str:
        """Return a pre-signed download URL for a sandbox file.

        The signature is minted by the API; ``expires_in_seconds``
        must be between 1 and :data:`SIGNED_FILE_MAX_TTL_SECONDS` (7 days).
        """
        return await self._presigned_url("GET", "/files/download", path, expires_in_seconds)

    async def presigned_upload_url(self, path: str, /, *, expires_in_seconds: int = 3600) -> str:
        """Return a pre-signed upload URL for a sandbox file.

        The signature is minted by the API; ``expires_in_seconds``
        must be between 1 and :data:`SIGNED_FILE_MAX_TTL_SECONDS` (7 days).
        """
        return await self._presigned_url("POST", "/files/upload", path, expires_in_seconds)

    async def write_many(self, files: Mapping[str, Buffer], /, *, atomic: bool = False) -> BulkWriteResult:
        """Write absolute paths mapped to bytes-like buffers, optionally atomically per file."""
        if not isinstance(files, Mapping):
            raise create_error(
                "files must be a mapping of paths to bytes-like values", 400, "BAD_REQUEST", "SDK"
            )
        contents = {
            sandbox_path(path, "files key"): buffer_bytes(content, f"files[{path!r}]")
            for path, content in files.items()
        }
        spec = file_bulk_upload_spec(contents, timeout=None, atomic=atomic)
        payload = await self._transport.send(spec)
        if isinstance(payload, bytes):
            raise create_error("expected JSON object", 400, "BAD_REQUEST", "SDK")
        return parse_bulk_write_result(payload)

    async def upload_file(
        self,
        local_path: str | os.PathLike[str],
        remote_path: str,
        /,
        *,
        on_progress: TransferProgressCallback | None = None,
        cancel: TransferCancelSignal | None = None,
        atomic: bool = False,
    ) -> None:
        """Stream a local file into the sandbox without buffering it whole in memory."""
        sandbox_path(remote_path, "remote_path")
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
        /,
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
        sandbox_path(remote_path, "remote_path")
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
        self, paths: Sequence[str], /, *, max_response_bytes: Optional[int] = None
    ) -> BulkReadResult:
        """Read multiple files, buffering each returned file in memory before returning.

        The whole response is held in memory. For large files, or content from a
        sandbox you do not trust, use :meth:`download_file`, which streams to disk
        with progress and cancellation.

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
        checked_paths = [
            sandbox_path(path, f"paths[{index}]") for index, path in enumerate(path_sequence(paths, "paths"))
        ]
        spec = bulk_download_spec(checked_paths, timeout=None)
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

    async def list(self, path: str, /, *, depth: Optional[int] = None) -> ListResult[FileInfo]:
        """List directory entries as typed ``FileInfo`` items (``items``, ``next_cursor``)."""
        params: Dict[str, JsonValue] = {"path": sandbox_path(path)}
        if depth is not None:
            if isinstance(depth, bool) or not isinstance(depth, int):
                raise create_error("depth must be an integer", 400, "BAD_REQUEST", "SDK")
            params["depth"] = depth
        raw_items, next_cursor = list_shape(
            await self._transport.send(RequestSpec("GET", "/files", params=params)),
            description="file list response",
        )
        items = [expect_file_info(item) for item in raw_items]
        return ListResult(items=items, next_cursor=next_cursor)

    async def stat(self, path: str, /) -> FileInfo:
        """Return file metadata."""
        return expect_file_info(
            await self._transport.send(RequestSpec("GET", "/files/info", params={"path": sandbox_path(path)}))
        )

    async def mkdir(self, path: str, /, *, mode: str = "0755") -> None:
        """Create a directory."""
        await self._transport.send(
            RequestSpec("POST", "/files/folder", params={"path": sandbox_path(path), "mode": mode})
        )

    async def remove(self, path: str, /, *, recursive: bool = False) -> None:
        """Remove a file, or a directory with ``recursive=True``."""
        await self._transport.send(
            RequestSpec("DELETE", "/files", params={"path": sandbox_path(path), "recursive": recursive})
        )

    async def move(self, source: str, destination: str, /) -> None:
        """Move or rename files."""
        await self._transport.send(
            RequestSpec(
                "POST",
                "/files/move",
                params={
                    "source": sandbox_path(source, "source"),
                    "destination": sandbox_path(destination, "destination"),
                },
            )
        )

    async def grep(self, path: str, pattern: str, /) -> FileSearchResult[FileMatch]:
        """Search file contents under ``path`` for the literal substring ``pattern``.

        Returns matches with one-based line numbers, as the sandbox user sees
        them. Directories and files below ``path`` that the sandbox user cannot
        read are not searched; ``skipped`` lists their absolute paths (at most
        1000). Only an unreadable ``path`` itself raises
        :class:`~daytona.v2.errors.FileAccessDeniedError`.
        """
        return parse_grep_response(
            await self._transport.send(
                RequestSpec("GET", "/files/find", params={"path": sandbox_path(path), "pattern": pattern})
            )
        )

    async def glob(self, path: str, pattern: str, /) -> FileSearchResult[str]:
        """Return the paths of files under ``path`` whose base name matches the glob ``pattern``.

        Directories below ``path`` that the sandbox user cannot read are not
        searched; ``skipped`` lists their absolute paths (at most 1000). Only an
        unreadable ``path`` itself raises
        :class:`~daytona.v2.errors.FileAccessDeniedError`.
        """
        return parse_glob_response(
            await self._transport.send(
                RequestSpec("GET", "/files/search", params={"path": sandbox_path(path), "pattern": pattern})
            )
        )

    async def replace(
        self, files: Sequence[str], pattern: str, new_value: str, /
    ) -> ListResult[ReplaceResult]:
        """Replace the literal substring ``pattern`` with ``new_value`` in ``files``."""
        checked_files = [
            sandbox_path(path, f"files[{index}]") for index, path in enumerate(path_sequence(files, "files"))
        ]
        items, next_cursor = list_shape(
            await self._transport.send(
                RequestSpec(
                    "POST",
                    "/files/replace",
                    json_body={"files": checked_files, "pattern": pattern, "newValue": new_value},
                )
            ),
            description="replace response",
        )
        return ListResult(items=[parse_replace_result(item) for item in items], next_cursor=next_cursor)

    async def chmod(
        self,
        path: str,
        /,
        *,
        mode: Optional[str] = None,
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> None:
        """Set file permissions and ownership."""
        params: Dict[str, JsonValue] = {"path": sandbox_path(path)}
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
        /,
        *,
        recursive: bool = False,
        on_overflow: Optional[WatchOverflowCallback] = None,
        on_exit: Optional[WatchExitCallback] = None,
    ) -> AsyncWatchHandle:
        """Watch ``path`` for changes, calling ``on_event`` for each one.

        Returns once the watch is established; callbacks are held until it has
        returned its handle (see :class:`AsyncWatchHandle` for stopping a watch
        from an event). Callbacks (plain or coroutine functions) run one at a
        time, in order.
        A failing ``on_event`` or
        ``on_overflow`` ends the watch with that error; ``on_exit`` runs once
        when the watch ends, and its own failure is ignored. A failure before
        the watch starts is raised from this call and does not call ``on_exit``;
        a stream that sends more than 4096 events, or more than 1 MiB, before
        ``start`` is one (``BadRequestError``). After ``start`` an event larger
        than the client's ``max_response_bytes`` ends the watch with
        ``PayloadTooLargeError``.
        """
        sandbox_path(path)
        if not isinstance(recursive, bool):
            raise create_error("recursive must be a boolean", 400, "BAD_REQUEST", "SDK")
        if not callable(on_event):
            raise create_error("on_event must be callable", 400, "BAD_REQUEST", "SDK")
        stream = await self._transport.open_stream(_watch_spec(path, recursive=recursive))
        handle = AsyncWatchHandle(stream, on_exit)
        ready = asyncio.Event()
        reader_error: list[Exception] = []
        callback_error: list[Exception] = []

        # The stream's bytes are counted as they arrive: before start the
        # whole stream (every frame, data-less and comment-only ones too) may
        # not pass WATCH_MAX_BYTES_BEFORE_START, after it each event is
        # bounded by the client's max_response_bytes.
        started = False

        def event_limit(completed: int) -> Optional[int]:
            if started:
                return client_limit or None
            remaining = WATCH_MAX_BYTES_BEFORE_START - completed
            return remaining if client_limit <= 0 else min(remaining, client_limit)

        def event_too_large(completed: int) -> Exception:
            remaining = WATCH_MAX_BYTES_BEFORE_START - completed
            if started or 0 < client_limit < remaining:
                return stream_event_too_large("filesystem watch stream", client_limit)
            return create_error(
                f"expected filesystem watch stream: it sent more than {WATCH_MAX_BYTES_BEFORE_START} bytes before start",
                400,
                "BAD_REQUEST",
                "SDK",
            )

        # Events read before start, delivered after it (none, from a
        # well-behaved toolbox): the reader must keep reading until start, but
        # holds at most WATCH_MAX_EVENTS_BEFORE_START of them.
        held: list[Callable[[], None | Awaitable[None]]] = []

        async def deliver(callback: Callable[[], None | Awaitable[None]] | None) -> None:
            # No callback runs before watch_dir() has handed out the handle
            # (events arriving with start wait here); a watch stopped
            # meanwhile delivers nothing more.
            await handle._published.wait()
            pending = [*held, *([] if callback is None else [callback])]
            held.clear()
            for next_callback in pending:
                if handle._stopped:
                    return
                try:
                    await handle._deliver(next_callback)
                except Exception as exc:
                    callback_error.append(exc)
                    raise

        client_limit = self._transport.max_response_bytes

        async def read_events() -> None:
            nonlocal started
            early_events = 0
            exit_error: Exception | None = None
            try:
                async for event_name, data, _id, _size in parse_sse_events_bounded(
                    stream.iter_bytes(), limit=event_limit, too_large=event_too_large
                ):
                    if handle._stopped:
                        break
                    callback: Callable[[], None | Awaitable[None]] | None = None
                    if event_name in ("fs", "overflow") and not started:
                        # Every listed event before start counts toward the
                        # cap, whether or not a callback handles it.
                        early_events += 1
                        if early_events > WATCH_MAX_EVENTS_BEFORE_START:
                            raise create_error(
                                f"expected filesystem watch stream: it sent more than "
                                f"{WATCH_MAX_EVENTS_BEFORE_START} events before start",
                                400,
                                "BAD_REQUEST",
                                "SDK",
                            )
                    if event_name == "start":
                        if not started:
                            started = True
                            ready.set()
                            if held:
                                await deliver(None)
                        continue
                    if event_name == "fs":
                        event = _parse_filesystem_event(sse_json("fs", data, "filesystem watch event"))
                        callback = functools.partial(on_event, event)
                    elif event_name == "overflow":
                        _check_overflow_event(data)
                        callback = on_overflow
                    if callback is None:
                        continue
                    if started:
                        await deliver(callback)
                    else:
                        held.append(callback)
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
            # Cancelled (or torn down) before watch_dir() returned, before or
            # after the start frame: the detached reader would otherwise keep
            # the HTTP stream open. No handle was returned, so the exit
            # callback never runs.
            handle._on_exit = None
            await handle.stop()
            raise
        if reader_error:
            raise reader_error[0]
        # Release the deliveries as the last step: asyncio runs nothing else
        # until the caller's next await, so it has stored the handle by then.
        # The synchronous twin publishes once it has built its own handle.
        if not on_a_bridge_loop():
            handle._publish()
        return handle

    async def _presigned_url(self, method: str, route: str, path: str, expires_in_seconds: int) -> str:
        sandbox_path(path)
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
    # Takes the raw decoded body, whatever JSON type it is: a response that
    # breaks the contract is ``BadRequestError`` naming the field, with the TS
    # SDK's messages (``readSignedFileUrl``).
    if not isinstance(payload, dict):
        raise create_error("signed file URL response is not a JSON object", 400, "BAD_REQUEST", "SDK")
    signature = payload.get("signature")
    expires_at = payload.get("expires_at")
    if not isinstance(signature, str) or not signature:
        raise create_error(
            "signed file URL response signature must be a non-empty string", 400, "BAD_REQUEST", "SDK"
        )
    if not isinstance(expires_at, str):
        raise create_error(
            "signed file URL response expires_at must be an RFC 3339 timestamp", 400, "BAD_REQUEST", "SDK"
        )
    try:
        parsed_expiry = parse_timestamp(expires_at, "signed file URL response: expires_at")
    except DaytonaError:
        raise create_error(
            "signed file URL response expires_at must be an RFC 3339 timestamp", 400, "BAD_REQUEST", "SDK"
        ) from None
    return _SignedFileUrl(signature=signature, expires=int(parsed_expiry.timestamp()))


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
