# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Directory watches on the synchronous client: callbacks run on the SDK's
event-loop thread, where they can stop and inspect their own watch."""

from __future__ import annotations

import asyncio
import threading
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
