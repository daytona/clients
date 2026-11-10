# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The SDK follows no redirect, in every transport: a 3xx answer is a
DaytonaError carrying its status (source SDK), and the Location is never
requested. The TypeScript twin is tests/redirects.test.ts."""

from __future__ import annotations

import os

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import DaytonaError, NotFoundError
from daytona.v2._core import error_from_response

pytestmark = pytest.mark.anyio


def _redirecting(status: int) -> FakeApi:
    # The redirect target would answer: a follower would turn the 3xx into a 404.
    return FakeApi(
        [
            PlannedResponse(status, "", {"Location": "/elsewhere", "Content-Type": "text/plain"}),
            PlannedResponse(404, {"code": "NOT_FOUND", "message": "followed"}),
        ]
    )


def _assert_redirect_error(error: BaseException, status: int) -> None:
    assert isinstance(error, DaytonaError)
    assert not isinstance(error, NotFoundError)
    assert (error.status_code, error.source, str(error)) == (
        status,
        "SDK",
        f"HTTP {status}: unexpected redirect (the SDK does not follow redirects)",
    )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_3xx_is_a_typed_error_and_is_never_followed(status: int) -> None:
    with _redirecting(status) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(DaytonaError) as raised:
                await root.v2.images.get("img")
        _assert_redirect_error(raised.value, status)
        assert [request.path for request in api.requests] == ["/images/img"]


def test_the_sync_client_refuses_a_3xx_too() -> None:
    with _redirecting(307) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        with Daytona(DaytonaConfig(api_key="key")) as root, pytest.raises(DaytonaError) as raised:
            root.v2.get("sbx")
        _assert_redirect_error(raised.value, 307)
        assert len(api.requests) == 1


@pytest.mark.parametrize(
    "headers",
    [
        # A body over the response cap, and one in an encoding the SDK refuses
        # undecoded: neither is read, so the redirect is the error.
        {"Content-Type": "text/plain", "Location": "/elsewhere"},
        {"Content-Type": "text/plain", "Location": "/elsewhere", "Content-Encoding": "br"},
        {"Content-Type": "text/plain", "Location": "/elsewhere", "Content-Encoding": "gzip"},
    ],
)
async def test_a_3xx_body_is_never_read(headers: dict[str, str]) -> None:
    with FakeApi([PlannedResponse(302, b"\x1f\x8b" + b"x" * 4096, headers)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key", max_response_bytes=16)) as root:
            with pytest.raises(DaytonaError) as raised:
                await root.v2.images.get("img")
        _assert_redirect_error(raised.value, 302)


def test_a_json_body_on_a_3xx_does_not_choose_the_error() -> None:
    error = error_from_response(304, b'{"code":"NOT_FOUND","message":"x"}', content_type="application/json")
    _assert_redirect_error(error, 304)


async def test_the_events_websocket_refuses_a_3xx_handshake() -> None:
    import asyncio

    from websockets.asyncio.server import ServerConnection, serve
    from websockets.exceptions import InvalidStatus

    from daytona.v2._events import _default_connector

    reached: list[str] = []

    async def target(connection: ServerConnection) -> None:
        reached.append(connection.request.path if connection.request is not None else "")
        await connection.send("hello")

    async with serve(target, "127.0.0.1", 0) as server:
        target_port = next(iter(server.sockets)).getsockname()[1]

        async def redirect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                f"HTTP/1.1 302 Found\r\nLocation: ws://127.0.0.1:{target_port}/stolen\r\n"
                "Content-Length: 0\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()

        redirector = await asyncio.start_server(redirect, "127.0.0.1", 0)
        async with redirector:
            port = redirector.sockets[0].getsockname()[1]
            with pytest.raises(InvalidStatus) as raised:
                await _default_connector(f"ws://127.0.0.1:{port}/events", ["daytona.events.v1"])
    assert raised.value.response.status_code == 302
    # The ticket-carrying handshake never reached the Location host.
    assert reached == []
