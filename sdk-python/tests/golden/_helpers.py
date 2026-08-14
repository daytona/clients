from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from daytona.common.errors import SOURCE_DAEMON, DaytonaError

ISO_8601_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
MUX_STDOUT = "\x01\x01\x01"
MUX_STDERR = "\x02\x02\x02"


def unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def assert_daemon_error(
    exc: DaytonaError,
    *,
    status_code: int,
    code: str,
    message: str,
) -> None:
    assert exc.status_code == status_code
    assert exc.code == code
    assert exc.source == SOURCE_DAEMON
    assert exc.message == message


def assert_iso8601_z(value: str) -> None:
    assert ISO_8601_Z_RE.match(value), value


def wait_until(predicate: Callable[[], bool], *, timeout: float = 10.0, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")


async def wait_until_async(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout: float = 10.0,
    interval: float = 0.2,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met before timeout")
