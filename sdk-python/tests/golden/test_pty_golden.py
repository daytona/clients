# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import threading
import time

import pytest

from daytona import PtySize
from daytona.common.errors import DaytonaProcessNotFoundError

from ._helpers import assert_iso8601_z, unique_name

pytestmark = [pytest.mark.e2e, pytest.mark.golden]


def test_pty_info_resize_list_and_unknown_id_shape(sandbox) -> None:
    pty_id = unique_name("golden-pty")
    handle = sandbox.process.create_pty_session(pty_id, pty_size=PtySize(cols=80, rows=24))

    try:
        info = sandbox.process.get_pty_session_info(pty_id)
        resized = sandbox.process.resize_pty_session(pty_id, PtySize(cols=100, rows=30))
        listed = sandbox.process.list_pty_sessions()
    finally:
        handle.kill()
        handle.wait()
        handle.disconnect()

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
        sandbox.process.get_pty_session_info("does-not-exist")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PROCESS_NOT_FOUND"


def test_pty_connect_echo_and_kill_wait_semantics(sandbox) -> None:
    pty_id = unique_name("golden-pty")
    first_handle = sandbox.process.create_pty_session(pty_id, pty_size=PtySize(cols=80, rows=24))
    first_handle.disconnect()
    connected = sandbox.process.connect_pty_session(pty_id)
    chunks: list[str] = []

    def kill_later() -> None:
        time.sleep(2)
        sandbox.process.kill_pty_session(pty_id)

    threading.Thread(target=kill_later, daemon=True).start()
    connected.send_input("echo SECOND\n")
    result = connected.wait(on_data=lambda data: chunks.append(data.decode("utf-8", errors="replace")))
    joined = "".join(chunks)
    connected.disconnect()

    assert "echo SECOND" in joined
    assert "SECOND\r\n" in joined
    assert result.exit_code == 137
    assert result.error == "SIGKILL"
