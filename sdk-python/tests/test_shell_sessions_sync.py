# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

import respx

from daytona.v2 import ProcessHandle, Session
from daytona.v2._bridge import SyncBridge
from daytona.v2._sync.process import ProcessService
from daytona.v2._transport import AsyncTransport
from daytona.v2.process import AsyncProcessService


def test_sync_session_wraps_commands_when_used_as_context_manager(respx_mock: respx.MockRouter) -> None:
    # Given an async service wrapped through the same bridge as the public client.
    base = "http://toolbox.invalid"
    create = respx_mock.post(f"{base}/processes").respond(
        201,
        json={
            "id": "shell",
            "kind": "session",
            "state": "running",
            "pid": 42,
            "createdAt": "2026-10-20T00:00:00Z",
            "cwd": "/app",
            "stdin": "closed",
            "keepLogs": "until_cleanup",
            "truncatedHead": False,
        },
    )
    remove = respx_mock.delete(f"{base}/processes/shell").respond(204)
    bridge = SyncBridge()
    transport = AsyncTransport(base)
    process = ProcessService(AsyncProcessService("sandbox", transport), bridge)
    try:
        # When creating a session and starting a command through its sync twin.
        with process.create_session() as session:
            create.respond(
                201,
                json={
                    "id": "command",
                    "kind": "exec",
                    "state": "starting",
                    "sessionId": "shell",
                    "createdAt": "2026-10-20T00:00:00Z",
                    "cwd": "/app",
                    "stdin": "closed",
                    "keepLogs": "until_cleanup",
                    "truncatedHead": False,
                },
            )
            handle = session.start("echo ok", execution_timeout=5)
            # Then handles are synchronous and the command is bound to its shell.
            assert isinstance(session, Session)
            assert isinstance(handle, ProcessHandle)
            assert json.loads(create.calls.last.request.content)["sessionId"] == session.id
        assert remove.call_count == 1
    finally:
        bridge.call(transport.close)
        bridge.close()
