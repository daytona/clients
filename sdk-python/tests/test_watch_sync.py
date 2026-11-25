# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Directory watches on the synchronous client: callbacks run on the SDK's
event-loop thread, where they can stop and inspect their own watch."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Optional

import pytest

from daytona.v2 import FilesystemEvent
from daytona.v2._bridge import BridgeError, SyncBridge
from daytona.v2._core import RequestSpec
from daytona.v2._sync.filesystem import FileSystemService, WatchHandle
from daytona.v2.filesystem import AsyncFileSystemService


def _fs_frame(name: str) -> list[str]:
    return ["event: fs", f'data: {{"type":"create","name":"{name}","isDir":false,"timestamp":1}}', ""]


class _Stream:
    """The start frame, then (once ``release`` is set) every other frame at
    once, as frames buffered in one packet arrive."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.release = threading.Event()
        self.closed: Optional[asyncio.Event] = None

    async def iter_bytes(self):
        """The bytes of iter_lines, one LF per line."""
        async for line in self.iter_lines():
            yield (line + "\n").encode()

    async def iter_lines(self) -> AsyncIterator[str]:
        self.closed = asyncio.Event()
        for index, line in enumerate(self._lines):
            if index == 3:
                while not self.release.is_set():
                    await asyncio.sleep(0.005)
            yield line
        await self.closed.wait()

    async def close(self) -> None:
        if self.closed is not None:
            self.closed.set()


class _Transport:
    def __init__(self, stream: _Stream) -> None:
        self.stream = stream

    max_response_bytes = 0  # the client's cap (0 = no limit)

    async def open_stream(self, spec: RequestSpec) -> _Stream:
        return self.stream


@pytest.fixture
def bridge() -> Iterator[SyncBridge]:
    bridge = SyncBridge()
    try:
        yield bridge
    finally:
        bridge.close()


def _fs(bridge: SyncBridge, *names: str) -> tuple[FileSystemService, _Stream]:
    lines = ["event: start", "data: {}", ""] + [line for name in names for line in _fs_frame(name)]
    stream = _Stream(lines)
    return FileSystemService(AsyncFileSystemService(_Transport(stream)), bridge), stream  # type: ignore[arg-type]


def test_a_sync_callback_stops_its_own_watch_without_an_error(bridge: SyncBridge) -> None:
    fs, stream = _fs(bridge, "a.txt", "b.txt")
    seen: list[str] = []
    observed: list[object] = []
    handle: Optional[WatchHandle] = None
    exited = threading.Event()

    def on_event(event: FilesystemEvent) -> None:
        assert handle is not None
        seen.append(event.name)
        handle.stop()
        observed.append(handle.exit_error)
        observed.append(handle.wait_for_exit())

    handle = fs.watch_dir("/w", on_event, on_exit=lambda _error: exited.set())
    stream.release.set()

    assert exited.wait(timeout=5)
    # No event is delivered after the callback's stop(); nothing failed.
    assert seen == ["a.txt"]
    assert observed == [None, None]
    assert handle.wait_for_exit() is None
    assert handle.exit_error is None
    handle.stop()


def test_a_sync_on_exit_callback_can_stop_and_wait_for_its_own_watch(bridge: SyncBridge) -> None:
    fs, _stream = _fs(bridge)
    results: list[object] = []
    handle: Optional[WatchHandle] = None
    done = threading.Event()

    def on_exit(error: Optional[Exception]) -> None:
        assert handle is not None
        handle.stop()
        results.extend([error, handle.wait_for_exit(), handle.exit_error])
        done.set()

    handle = fs.watch_dir("/w", lambda _event: None, on_exit=on_exit)
    handle.stop()

    assert done.wait(timeout=5)
    assert results == [None, None, None]


def test_other_sync_calls_from_a_callback_end_the_watch_with_a_bridge_error(bridge: SyncBridge) -> None:
    fs, stream = _fs(bridge, "a.txt")

    def on_event(_event: FilesystemEvent) -> None:
        fs.list("/w")

    handle = fs.watch_dir("/w", on_event)
    stream.release.set()

    error = handle.wait_for_exit()
    assert isinstance(error, BridgeError)
    assert "cannot be called from a running event loop" in str(error)


def test_a_sync_callback_cannot_wait_for_or_stop_another_watch(bridge: SyncBridge) -> None:
    other_fs, _other_stream = _fs(bridge)
    other = other_fs.watch_dir("/other", lambda _event: None)
    for method in ("wait_for_exit", "stop"):
        fs, stream = _fs(bridge, "a.txt")

        def on_event(_event: FilesystemEvent, method: str = method) -> None:
            getattr(other, method)()

        handle = fs.watch_dir("/w", on_event)
        stream.release.set()

        error = handle.wait_for_exit()
        assert isinstance(error, BridgeError)
        assert f"{method}() on another watch" in str(error)
    assert other.exit_error is None
    other.stop()


def test_an_external_stop_is_not_starved_by_a_blocking_callback_and_its_backlog(bridge: SyncBridge) -> None:
    names = [f"f{index}.txt" for index in range(200)]
    fs, stream = _fs(bridge, *names)
    seen: list[str] = []
    exits: list[Optional[Exception]] = []
    in_flight = threading.Event()
    release = threading.Event()

    def on_event(event: FilesystemEvent) -> None:
        seen.append(event.name)
        if len(seen) == 1:
            in_flight.set()
            # Blocks the SDK's loop thread, as a slow synchronous callback does.
            assert release.wait(timeout=5)
        else:
            time.sleep(0.01)

    handle = fs.watch_dir("/w", on_event, on_exit=exits.append)
    stream.release.set()
    assert in_flight.wait(timeout=5)

    stopper = threading.Thread(target=handle.stop)
    stopper.start()
    # stop() is waiting for the callback in flight, not running the backlog.
    stopper.join(timeout=0.2)
    assert stopper.is_alive()
    release.set()
    started = time.monotonic()
    stopper.join(timeout=5)

    assert not stopper.is_alive()
    assert time.monotonic() - started < 1
    # Nothing after the callback in flight; on_exit ran before stop() returned.
    assert seen == ["f0.txt"]
    assert exits == [None]
    assert handle.exit_error is None


def test_other_sync_calls_run_between_the_deliveries_of_a_backlog(bridge: SyncBridge) -> None:
    names = [f"f{index}.txt" for index in range(1000)]
    fs, stream = _fs(bridge, *names)
    seen: list[str] = []
    first = threading.Event()

    def on_event(event: FilesystemEvent) -> None:
        seen.append(event.name)
        first.set()
        time.sleep(0.002)

    handle = fs.watch_dir("/w", on_event)
    stream.release.set()
    assert first.wait(timeout=5)

    # exit_error is read on the loop: it waits for one delivery, not for the backlog.
    assert handle.exit_error is None
    delivered = len(seen)
    handle.stop()

    assert delivered < len(names) // 2
    assert len(seen) < len(names)


@pytest.mark.anyio
async def test_an_async_watch_backlog_of_plain_callbacks_lets_other_tasks_run() -> None:
    names = [f"f{index}.txt" for index in range(50)]
    stream = _Stream(["event: start", "data: {}", ""] + [line for name in names for line in _fs_frame(name)])
    fs = AsyncFileSystemService(_Transport(stream))  # type: ignore[arg-type]
    seen: list[str] = []
    observed: list[int] = []
    done = asyncio.Event()

    def on_event(event: FilesystemEvent) -> None:
        seen.append(event.name)
        if len(seen) == len(names):
            done.set()

    async def ticker() -> None:
        while not done.is_set():
            observed.append(len(seen))
            await asyncio.sleep(0)

    handle = await fs.watch_dir("/w", on_event)
    tick = asyncio.create_task(ticker())
    stream.release.set()
    await asyncio.wait_for(done.wait(), timeout=5)
    await tick
    await handle.stop()

    assert any(0 < count < len(names) for count in observed)
    assert seen == names
