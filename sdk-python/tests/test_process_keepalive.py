# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from wsproto.events import TextMessage

from daytona.common.pty import PTY_EXIT_CONTROL_SUBPROTOCOL, WS_KEEPALIVE_INTERVAL_SECONDS

_SERIALIZED = (
    "GET",
    "https://toolbox.example/process/pty/pty-1/connect",
    {"Authorization": "Bearer t"},
    None,
)


class TestSyncPtyKeepalive:
    def test_connect_pty_session_enables_httpx_ws_keepalive(self):
        from daytona._sync.process import Process

        api_client = MagicMock()
        api_client._connect_pty_session_serialize.return_value = _SERIALIZED
        http_client = MagicMock()
        process = Process("python", api_client, http_client=http_client)

        ws = MagicMock()
        ws.receive.side_effect = [
            TextMessage(
                data='{"type":"control","status":"connected"}',
                frame_finished=True,
                message_finished=True,
            )
        ]
        ws_cm = MagicMock()
        ws_cm.__enter__.return_value = ws
        with patch("daytona._sync.process.httpx_ws.connect_ws", return_value=ws_cm) as connect_ws:
            process.connect_pty_session("pty-1")

        kwargs = connect_ws.call_args.kwargs
        assert connect_ws.call_args.args[0] == "wss://toolbox.example/process/pty/pty-1/connect"
        assert kwargs["subprotocols"] == [PTY_EXIT_CONTROL_SUBPROTOCOL]
        assert kwargs["keepalive_ping_interval_seconds"] == WS_KEEPALIVE_INTERVAL_SECONDS
        assert kwargs["keepalive_ping_timeout_seconds"] == WS_KEEPALIVE_INTERVAL_SECONDS


class TestAsyncPtyKeepalive:
    @pytest.mark.asyncio
    async def test_open_ws_enables_aiohttp_heartbeat(self):
        from daytona._async.process import AsyncProcess

        ws = AsyncMock()
        ws.closed = False
        session = AsyncMock(spec=aiohttp.ClientSession)
        session.ws_connect = AsyncMock(return_value=ws)
        api_client = MagicMock()
        api_client.api_client.http_session = session
        process = AsyncProcess("python", api_client)

        result = await process._open_ws("wss://toolbox.example/pty", {"Authorization": "Bearer t"})

        assert result is ws
        kwargs = session.ws_connect.call_args.kwargs
        assert kwargs["heartbeat"] == WS_KEEPALIVE_INTERVAL_SECONDS
