# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

import pytest

from daytona import PtySize
from daytona.common.errors import DaytonaProcessNotFoundError

from ._helpers import assert_iso8601_z, unique_name

pytestmark = [pytest.mark.e2e, pytest.mark.golden, pytest.mark.asyncio(loop_scope="module")]


async def test_async_pty_info_resize_list_and_unknown_id_shape(async_sandbox) -> None:
    pty_id = unique_name("golden-pty")
    handle = await async_sandbox.process.create_pty_session(
        pty_id, lambda _data: None, pty_size=PtySize(cols=80, rows=24)
    )

    try:
        info = await async_sandbox.process.get_pty_session_info(pty_id)
        resized = await async_sandbox.process.resize_pty_session(pty_id, PtySize(cols=100, rows=30))
        listed = await async_sandbox.process.list_pty_sessions()
    finally:
        await handle.kill()
        await handle.wait()
        await handle.disconnect()

    assert info.id == pty_id
    assert info.cwd == "/home/daytona"
    assert info.envs["TERM"] == "xterm-256color"
    assert info.cols == 80
    assert info.rows == 24
    assert info.active is True
    assert info.lazy_start is False
    assert_iso8601_z(info.created_at)
    assert resized.cols == 100
    assert resized.rows == 30
    assert any(item.id == pty_id for item in listed)

    with pytest.raises(DaytonaProcessNotFoundError) as exc_info:
        await async_sandbox.process.get_pty_session_info("does-not-exist")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PROCESS_NOT_FOUND"


async def test_async_pty_connect_echo_and_kill_wait_semantics(async_sandbox) -> None:
    pty_id = unique_name("golden-pty")
    first_handle = await async_sandbox.process.create_pty_session(
        pty_id, lambda _data: None, pty_size=PtySize(cols=80, rows=24)
    )
    await first_handle.disconnect()
    chunks: list[str] = []

    async def on_data(data: bytes) -> None:
        chunks.append(data.decode("utf-8", errors="replace"))

    connected = await async_sandbox.process.connect_pty_session(pty_id, on_data)

    async def kill_later() -> None:
        await asyncio.sleep(2)
        await async_sandbox.process.kill_pty_session(pty_id)

    kill_task = asyncio.create_task(kill_later())
    await connected.send_input("echo SECOND\n")
    result = await connected.wait()
    await kill_task
    joined = "".join(chunks)
    await connected.disconnect()

    assert "echo SECOND" in joined
    assert "SECOND\r\n" in joined
    assert result.exit_code == 137
    assert result.error == "SIGKILL"
