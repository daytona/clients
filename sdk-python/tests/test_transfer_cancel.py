# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""File transfers wait on the network without needless wake-ups: with no
cancel signal they await it directly, and a ``threading.Event`` wakes them
when set, from any thread, without polling. An ``asyncio.Event`` wakes them
when set on the transfer's loop and is also polled, since setting it from
another thread does not wake that loop; other ``is_set()`` objects are
polled. Every helper the watch starts ends with the transfer, and the watch
never hides a cancellation of the caller's task."""

from __future__ import annotations

import asyncio
import threading
import types
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest
from test_fs_transfer import (
    DownloadStream,
    DownloadTransport,
    NeverOpeningDownloadTransport,
    NeverYieldingDownloadStream,
    StalledAfterBodyTransport,
)

import daytona.v2._transfer_cancel as transfer_cancel
from daytona.v2 import TransferCancelledError
from daytona.v2._bridge import SyncBridge
from daytona.v2._sync.filesystem import FileSystemService
from daytona.v2._transfer_cancel import TransferCancelWatch
from daytona.v2.filesystem import AsyncFileSystemService

# Long enough that a test relying on a poll instead of a notification
# cannot pass within its bound.
_NO_POLL = 3600.0
_BOUND = 10.0


class _Flag:
    """A cancel signal with only ``is_set()``: nothing can wait on it."""

    def __init__(self) -> None:
        self.value = False

    def is_set(self) -> bool:
        return self.value


class _EventSubclass(threading.Event):
    pass


def _helper_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "daytona-transfer-cancel"]


@pytest.fixture
def no_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transfer_cancel, "POLL_SECONDS", _NO_POLL)


@pytest.mark.anyio
async def test_without_a_signal_each_step_is_awaited_in_the_callers_task() -> None:
    caller = asyncio.current_task()
    tasks: list[Optional[asyncio.Task[object]]] = []

    async def step() -> str:
        tasks.append(asyncio.current_task())
        return "done"

    async with TransferCancelWatch(None) as watch:
        assert await watch.run(step(), operation="download") == "done"
        assert watch.polling is False
    assert tasks == [caller]


@pytest.mark.anyio
async def test_download_without_a_signal_reads_every_chunk_in_the_callers_task(tmp_path: Path) -> None:
    caller = asyncio.current_task()
    readers: list[Optional[asyncio.Task[object]]] = []

    class RecordingStream(DownloadStream):
        async def iter_bytes(self) -> AsyncIterator[bytes]:
            for chunk in (b"ab", b"cd"):
                readers.append(asyncio.current_task())
                yield chunk

    stream = RecordingStream([], headers={"Content-Length": "4"})
    fs = AsyncFileSystemService(DownloadTransport(stream))  # type: ignore[arg-type]

    await fs.download_file("/remote.bin", tmp_path / "out.bin")

    assert (tmp_path / "out.bin").read_bytes() == b"abcd"
    assert readers == [caller, caller]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["asyncio", "threading"])
async def test_events_notify_and_only_a_threading_event_is_not_polled(kind: str, no_polling: None) -> None:
    cancel = asyncio.Event() if kind == "asyncio" else threading.Event()
    async with TransferCancelWatch(cancel) as watch:
        assert watch.notifies is True
        assert watch.polling is (kind == "asyncio")


@pytest.mark.anyio
async def test_a_threading_event_on_an_unchecked_python_falls_back_to_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transfer_cancel, "_THREAD_EVENT_CHECKED_THROUGH", (3, 0))
    async with TransferCancelWatch(threading.Event()) as watch:
        assert watch.notifies is False
        assert watch.polling is True
    assert _helper_threads() == []


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [_Flag(), _EventSubclass()], ids=["is_set-only", "Event-subclass"])
async def test_other_signals_fall_back_to_polling(cancel: object) -> None:
    async with TransferCancelWatch(cancel) as watch:  # type: ignore[arg-type]
        assert watch.polling is True
    assert _helper_threads() == []


@pytest.mark.anyio
@pytest.mark.parametrize("internals", ["no condition", "no waiter queue"])
async def test_a_threading_event_with_unknown_internals_falls_back_to_polling(internals: str) -> None:
    cancel = threading.Event()
    if internals == "no condition":
        cancel._cond = object()  # type: ignore[attr-defined]
    else:
        cancel._cond._waiters = []  # type: ignore[attr-defined]
    async with TransferCancelWatch(cancel) as watch:
        assert watch.polling is True
    assert _helper_threads() == []


@pytest.mark.anyio
async def test_a_threading_event_on_another_implementation_falls_back_to_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transfer_cancel.sys, "implementation", types.SimpleNamespace(name="pypy"))
    async with TransferCancelWatch(threading.Event()) as watch:
        assert watch.notifies is False
        assert watch.polling is True
    assert _helper_threads() == []


@pytest.mark.anyio
async def test_a_threading_event_falls_back_to_polling_when_no_thread_can_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transfer_cancel, "POLL_SECONDS", 0.001)

    def refuse(thread: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(transfer_cancel._ThreadEventWatch, "_start_thread", staticmethod(refuse))
    cancel = threading.Event()
    async with TransferCancelWatch(cancel) as watch:
        assert watch.notifies is False
        assert watch.polling is True
        assert not cancel._cond._waiters  # type: ignore[attr-defined]
        step = asyncio.ensure_future(watch.run(asyncio.Event().wait(), operation="download"))
        await asyncio.sleep(0.01)
        cancel.set()
        with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
            await asyncio.wait_for(step, timeout=_BOUND)


@pytest.mark.anyio
async def test_asyncio_event_cancels_a_stalled_download_without_polling(
    tmp_path: Path, no_polling: None
) -> None:
    destination = tmp_path / "download.bin"
    stream = NeverYieldingDownloadStream()
    fs = AsyncFileSystemService(DownloadTransport(stream))  # type: ignore[arg-type]
    cancel = asyncio.Event()
    task = asyncio.create_task(fs.download_file("/remote.bin", destination, cancel=cancel))
    await asyncio.wait_for(stream.started.wait(), timeout=_BOUND)

    cancel.set()

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await asyncio.wait_for(task, timeout=_BOUND)
    assert stream.closed is True
    assert not destination.exists()
    assert list(tmp_path.glob(".daytona-dl-*")) == []


@pytest.mark.anyio
async def test_threading_event_set_from_another_thread_cancels_a_stalled_download(
    tmp_path: Path, no_polling: None
) -> None:
    destination = tmp_path / "download.bin"
    stream = NeverYieldingDownloadStream()
    fs = AsyncFileSystemService(DownloadTransport(stream))  # type: ignore[arg-type]
    cancel = threading.Event()
    task = asyncio.create_task(fs.download_file("/remote.bin", destination, cancel=cancel))
    await asyncio.wait_for(stream.started.wait(), timeout=_BOUND)
    assert len(_helper_threads()) == 1

    await asyncio.to_thread(cancel.set)

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await asyncio.wait_for(task, timeout=_BOUND)
    assert stream.closed is True
    assert list(tmp_path.glob(".daytona-dl-*")) == []
    assert _helper_threads() == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["asyncio", "threading"])
async def test_an_event_cancels_a_stalled_header_read(kind: str, tmp_path: Path, no_polling: None) -> None:
    transport = NeverOpeningDownloadTransport()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    cancel = asyncio.Event() if kind == "asyncio" else threading.Event()
    task = asyncio.create_task(fs.download_file("/remote.bin", tmp_path / "out.bin", cancel=cancel))
    await asyncio.wait_for(transport.started.wait(), timeout=_BOUND)

    await asyncio.to_thread(cancel.set) if isinstance(cancel, threading.Event) else cancel.set()

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await asyncio.wait_for(task, timeout=_BOUND)
    await asyncio.wait_for(transport.cancelled.wait(), timeout=_BOUND)
    assert list(tmp_path.glob(".daytona-dl-*")) == []
    assert _helper_threads() == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["asyncio", "threading"])
async def test_an_event_cancels_a_stalled_upload(kind: str, tmp_path: Path, no_polling: None) -> None:
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abc")
    transport = StalledAfterBodyTransport()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    cancel = asyncio.Event() if kind == "asyncio" else threading.Event()
    task = asyncio.create_task(fs.upload_file(local_path, "/remote.bin", cancel=cancel))
    await asyncio.wait_for(transport.drained.wait(), timeout=_BOUND)

    await asyncio.to_thread(cancel.set) if isinstance(cancel, threading.Event) else cancel.set()

    with pytest.raises(TransferCancelledError, match="upload transfer cancelled"):
        await asyncio.wait_for(task, timeout=_BOUND)
    assert transport.cancelled, "the in-flight request must be torn down when cancellation wins"
    assert _helper_threads() == []


@pytest.mark.anyio
async def test_an_is_set_only_signal_still_cancels_a_stalled_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer_cancel, "POLL_SECONDS", 0.001)
    stream = NeverYieldingDownloadStream()
    fs = AsyncFileSystemService(DownloadTransport(stream))  # type: ignore[arg-type]
    cancel = _Flag()
    task = asyncio.create_task(fs.download_file("/remote.bin", tmp_path / "out.bin", cancel=cancel))
    await asyncio.wait_for(stream.started.wait(), timeout=_BOUND)

    cancel.value = True

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await asyncio.wait_for(task, timeout=_BOUND)
    assert stream.closed is True
    assert list(tmp_path.glob(".daytona-dl-*")) == []


@pytest.mark.anyio
async def test_an_asyncio_event_bound_to_another_loop_falls_back_to_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transfer_cancel, "POLL_SECONDS", 0.001)
    cancel = asyncio.Event()

    async def bind() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(cancel.wait(), timeout=0.001)

    await asyncio.to_thread(asyncio.run, bind())
    stalled = asyncio.Event()
    async with TransferCancelWatch(cancel) as watch:
        assert watch.notifies is True
        step = asyncio.create_task(watch.run(stalled.wait(), operation="download"))
        # The notifier fails (the event belongs to the other loop) while the
        # signal reads unset: the watch drops it and keeps polling.
        for _ in range(1000):
            if not watch.notifies:
                break
            await asyncio.sleep(0)
        assert watch.notifies is False
        assert watch.polling is True
        assert not step.done()

        cancel.set()

        with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
            await asyncio.wait_for(step, timeout=_BOUND)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["asyncio", "threading"])
async def test_a_completed_transfer_leaves_no_waiter_behind(kind: str, tmp_path: Path) -> None:
    stream = DownloadStream([b"ab"], headers={"Content-Length": "2"})
    fs = AsyncFileSystemService(DownloadTransport(stream))
    cancel = asyncio.Event() if kind == "asyncio" else threading.Event()

    await fs.download_file("/remote.bin", tmp_path / "out.bin", cancel=cancel)

    assert (tmp_path / "out.bin").read_bytes() == b"ab"
    assert _helper_threads() == []
    if isinstance(cancel, threading.Event):
        assert not cancel._cond._waiters  # type: ignore[attr-defined]
    else:
        assert not cancel._waiters  # type: ignore[attr-defined]
    cancel.set()
    assert cancel.is_set()


@pytest.mark.anyio
async def test_a_set_threading_event_still_wakes_the_other_waiters_on_it() -> None:
    cancel = threading.Event()
    other = asyncio.create_task(asyncio.to_thread(cancel.wait, _BOUND))
    waiters = cancel._cond._waiters  # type: ignore[attr-defined]
    async with TransferCancelWatch(cancel):
        # Both the other thread and the watch are parked on the event before it is set.
        for _ in range(1000):
            if len(waiters) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(waiters) == 2
        await asyncio.to_thread(cancel.set)
    assert await asyncio.wait_for(other, timeout=_BOUND) is True
    assert _helper_threads() == []


@pytest.mark.anyio
async def test_a_preset_threading_event_is_reported_at_once(no_polling: None) -> None:
    cancel = threading.Event()
    cancel.set()
    async with TransferCancelWatch(cancel) as watch:
        with pytest.raises(TransferCancelledError, match="upload transfer cancelled"):
            await asyncio.wait_for(watch.run(asyncio.Event().wait(), operation="upload"), timeout=_BOUND)
    assert _helper_threads() == []


def test_sync_client_download_stops_when_a_threading_event_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer_cancel, "POLL_SECONDS", _NO_POLL)
    reading = threading.Event()

    class StalledStream:
        def __init__(self) -> None:
            self.headers = {"Content-Length": "6"}
            self.closed = False

        async def iter_bytes(self) -> AsyncIterator[bytes]:
            reading.set()
            await asyncio.Event().wait()
            yield b"never"

        async def close(self) -> None:
            self.closed = True

    stream = StalledStream()
    bridge = SyncBridge()
    fs = FileSystemService(AsyncFileSystemService(DownloadTransport(stream)), bridge)  # type: ignore[arg-type]
    cancel = threading.Event()
    raised: list[BaseException] = []

    def download() -> None:
        try:
            fs.download_file("/remote.bin", tmp_path / "out.bin", cancel=cancel)
        except BaseException as error:
            raised.append(error)

    worker = threading.Thread(target=download)
    worker.start()
    try:
        assert reading.wait(_BOUND)
        cancel.set()
        worker.join(_BOUND)
        assert not worker.is_alive(), "setting the event must stop the stalled download"
    finally:
        bridge.close()
    assert len(raised) == 1 and isinstance(raised[0], TransferCancelledError)
    assert stream.closed is True
    assert list(tmp_path.glob(".daytona-dl-*")) == []
    assert _helper_threads() == []


def _stalled_stream(reading: threading.Event) -> object:
    class StalledStream:
        def __init__(self) -> None:
            self.headers = {"Content-Length": "6"}
            self.closed = False

        async def iter_bytes(self) -> AsyncIterator[bytes]:
            reading.set()
            await asyncio.Event().wait()
            yield b"never"

        async def close(self) -> None:
            self.closed = True

    return StalledStream()


def test_sync_client_download_stops_when_an_asyncio_event_is_set_from_another_thread(
    tmp_path: Path,
) -> None:
    # asyncio.Event.set() off the bridge loop does not wake that loop: the
    # 50 ms check is what notices it.
    reading = threading.Event()
    stream = _stalled_stream(reading)
    bridge = SyncBridge()
    fs = FileSystemService(AsyncFileSystemService(DownloadTransport(stream)), bridge)  # type: ignore[arg-type]
    cancel = asyncio.Event()
    raised: list[BaseException] = []

    def download() -> None:
        try:
            fs.download_file("/remote.bin", tmp_path / "out.bin", cancel=cancel)
        except BaseException as error:
            raised.append(error)

    worker = threading.Thread(target=download)
    worker.start()
    try:
        assert reading.wait(_BOUND)
        cancel.set()
        worker.join(_BOUND)
        assert not worker.is_alive(), "setting the event must stop the stalled download"
    finally:
        bridge.close()
    assert len(raised) == 1 and isinstance(raised[0], TransferCancelledError)
    assert stream.closed is True  # type: ignore[attr-defined]
    assert list(tmp_path.glob(".daytona-dl-*")) == []


@pytest.mark.anyio
async def test_async_download_stops_when_an_asyncio_event_is_set_from_another_thread(tmp_path: Path) -> None:
    stream = NeverYieldingDownloadStream()
    fs = AsyncFileSystemService(DownloadTransport(stream))  # type: ignore[arg-type]
    cancel = asyncio.Event()
    task = asyncio.create_task(fs.download_file("/remote.bin", tmp_path / "out.bin", cancel=cancel))
    await asyncio.wait_for(stream.started.wait(), timeout=_BOUND)

    # Set off the loop, with nothing else due to wake it: only the periodic
    # check can notice.
    setter = threading.Timer(0.05, cancel.set)
    setter.start()
    try:
        with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
            await asyncio.wait_for(task, timeout=_BOUND)
    finally:
        setter.join(_BOUND)
    assert stream.closed is True


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["asyncio", "threading", "is_set-only"])
async def test_leaving_the_watch_never_hides_a_cancellation_of_the_callers_task(kind: str) -> None:
    cancel: object = {"asyncio": asyncio.Event, "threading": threading.Event, "is_set-only": _Flag}[kind]()
    after: list[str] = []

    async def body() -> None:
        current = asyncio.current_task()
        assert current is not None
        async with TransferCancelWatch(cancel):  # type: ignore[arg-type]
            asyncio.get_running_loop().call_soon(current.cancel)
        await asyncio.sleep(_BOUND)
        after.append("ran past the cancellation")

    task = asyncio.create_task(body())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=_BOUND * 2)
    assert after == []
    assert _helper_threads() == []


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["asyncio", "threading", "is_set-only"])
async def test_a_cancelled_step_never_hides_a_cancellation_of_the_callers_task(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The signal stops the transfer; while the stalled step unwinds, the
    # caller's own task is cancelled. That cancellation must reach the caller.
    monkeypatch.setattr(transfer_cancel, "POLL_SECONDS", 0.001)
    signal = {"asyncio": asyncio.Event, "threading": threading.Event, "is_set-only": _Flag}[kind]()
    started = asyncio.Event()
    unwinding = asyncio.Event()
    unwound = asyncio.Event()
    after: list[str] = []

    async def step() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Unwinding takes a while, as closing a connection does; it ends
            # once the caller's task has been cancelled.
            unwinding.set()
            await unwound.wait()
            raise

    async def body() -> None:
        async with TransferCancelWatch(signal) as watch:  # type: ignore[arg-type]
            try:
                await watch.run(step(), operation="download")
            except TransferCancelledError:
                after.append("transfer cancelled")
        await asyncio.sleep(_BOUND)
        after.append("ran past the cancellation")

    task = asyncio.create_task(body())
    await asyncio.wait_for(started.wait(), timeout=_BOUND)
    if isinstance(signal, _Flag):
        signal.value = True
    elif isinstance(signal, threading.Event):
        await asyncio.to_thread(signal.set)
    else:
        signal.set()
    await asyncio.wait_for(unwinding.wait(), timeout=_BOUND)
    task.cancel()
    unwound.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=_BOUND * 2)
    assert after == []
