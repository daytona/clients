# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client-identity fakes pin exclusive-stream ownership without a test server.

Build-log streams (``AsyncTransport.open_exclusive_stream``) run on a client of
their own on an SDK-owned transport, so closing or cancelling one observation
can never touch the pool other requests use. An injected client is borrowed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anyio
import httpx
import pytest

from daytona.v2._auth import ProjectTokenAuth
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import BadRequestError, ClientClosedError

pytestmark = pytest.mark.anyio


class Body(httpx.AsyncByteStream):
    def __init__(self, *, hang: bool = False) -> None:
        self.hang = hang
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"peer still works"
        if self.hang:
            await anyio.sleep(30)

    async def aclose(self) -> None:
        self.closed = True


class Owner:
    """One fake connection pool: the client plus every body it served."""

    def __init__(self, header_timeouts: list[object], *, hang: bool = False) -> None:
        self.bodies: list[Body] = []

        def handle(request: httpx.Request) -> httpx.Response:
            header_timeouts.append(request.extensions["timeout"]["read"])
            body = Body(hang=hang and request.url.path != "/ordinary")
            self.bodies.append(body)
            return httpx.Response(200, stream=body)

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def read_all(stream) -> bytes:
    return b"".join([chunk async for chunk in stream.iter_bytes()])


async def test_borrowed_client_streams_close_only_their_response():
    owner = Owner([])
    transport = AsyncTransport("https://api.invalid", client=owner.client)
    first = await transport.scoped("https://api.invalid").open_exclusive_stream(RequestSpec("GET", "/first"))
    peer = await transport.open_exclusive_stream(RequestSpec("GET", "/peer"))
    try:
        await first.close()
        assert owner.bodies[0].closed and not owner.bodies[1].closed
        assert await read_all(peer) == b"peer still works"
        await peer.close()
        # Ordinary requests on the injected shared client still succeed, and
        # the SDK never closes a client it did not create.
        assert await transport.raw(RequestSpec("GET", "/ordinary")) == b"peer still works"
        assert not owner.client.is_closed
    finally:
        await first.close()
        await peer.close()
        await transport.close()
        assert not owner.client.is_closed
        await owner.client.aclose()


async def test_default_and_scoped_streams_own_distinct_clients_and_cancel_only_their_response(monkeypatch):
    owners: list[Owner] = []
    header_timeouts: list[object] = []

    def fresh_client(_transport: AsyncTransport) -> httpx.AsyncClient:
        owner = Owner(header_timeouts, hang=True)
        owners.append(owner)
        return owner.client

    monkeypatch.setattr(AsyncTransport, "_new_stream_client", fresh_client)
    transport = AsyncTransport("https://api.invalid")  # owners[0] is its shared pool
    first = await transport.open_exclusive_stream(
        RequestSpec("GET", "/first", timeout=0.05, unbounded_read=True)
    )
    second = await transport.scoped("https://api.invalid").open_exclusive_stream(
        RequestSpec("GET", "/second", timeout=0.05, unbounded_read=True)
    )
    shared, first_owner, second_owner = owners
    try:
        assert len({id(owner.client) for owner in owners}) == 3
        # Header deadlines are sent; only the idle read bound is dropped after.
        assert header_timeouts == [0.05, 0.05]

        async def cancelled_reader() -> None:
            try:
                await read_all(first)
            finally:
                await first.close()

        with anyio.move_on_after(0.05):
            await cancelled_reader()
        assert first_owner.client.is_closed and first_owner.bodies[0].closed
        assert not second_owner.client.is_closed and not second_owner.bodies[0].closed
        assert not shared.client.is_closed
        assert await transport.raw(RequestSpec("GET", "/ordinary")) == b"peer still works"
        await second.close()
        assert second_owner.client.is_closed
    finally:
        await first.close()
        await second.close()
        await transport.close()


async def test_completed_discovery_stream_closes_its_factory_client_not_the_shared_one():
    owner = Owner([])
    async with httpx.AsyncClient() as shared:
        transport = AsyncTransport(
            "https://api.invalid",
            client=shared,
            stream_client_factory=lambda: owner.client,
        )
        stream = await transport.open_exclusive_stream(RequestSpec("GET", "/discovery", timeout=0.05))
        try:
            assert await read_all(stream) == b"peer still works"
        finally:
            await stream.close()
        assert owner.client.is_closed and not shared.is_closed


async def test_factory_returning_the_shared_client_is_refused():
    async with httpx.AsyncClient() as shared:
        transport = AsyncTransport("https://api.invalid", client=shared, stream_client_factory=lambda: shared)
        with pytest.raises(ValueError, match="fresh"):
            await transport.open_exclusive_stream(RequestSpec("GET", "/unsafe"))
        assert not shared.is_closed


async def test_exclusive_stream_closes_owned_client_when_auth_token_fetch_fails(monkeypatch):
    async with httpx.AsyncClient() as shared:
        factory_calls = 0

        def count_factory_call() -> httpx.AsyncClient:
            nonlocal factory_calls
            factory_calls += 1
            return httpx.AsyncClient()

        auth = ProjectTokenAuth(
            api_url="https://api.invalid",
            jwt_token="jwt",
            project_id="project-1",
            source="sdk-python-async",
            client=shared,
        )

        async def fail_get_token(*, force_refresh: bool = False, current_token: str | None = None) -> str:
            del force_refresh, current_token
            raise BadRequestError("boom", status_code=400, code="BAD_REQUEST", source="SDK")

        monkeypatch.setattr(auth, "get_token", fail_get_token)
        transport = AsyncTransport(
            "https://api.invalid",
            client=shared,
            stream_client_factory=count_factory_call,
            auth=auth,
        )

        with pytest.raises(BadRequestError, match="boom"):
            await transport.open_exclusive_stream(RequestSpec("GET", "/discovery", timeout=0.05))

        assert factory_calls == 0
        assert not shared.is_closed


async def test_exclusive_stream_retries_project_token_auth_once_on_401():
    exchange_calls = 0
    seen: list[object] = []
    bodies: list[Body] = []

    def auth_handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_calls
        if request.url.path != "/auth/token":
            raise AssertionError(request.url.path)
        exchange_calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "access_token": f"platform-{exchange_calls}",
                "expires_in": 300,
                "token_type": "Bearer",
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
        )

    def stream_client() -> httpx.AsyncClient:
        def stream_handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            body = Body()
            bodies.append(body)
            status = 200 if request.headers.get("authorization") == "Bearer platform-2" else 401
            return httpx.Response(status, stream=body)

        return httpx.AsyncClient(transport=httpx.MockTransport(stream_handler))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(auth_handler), base_url="https://api.invalid"
    ) as shared:
        auth = ProjectTokenAuth(
            api_url="https://api.invalid",
            jwt_token="jwt",
            project_id="project-1",
            source="sdk-python-async",
            client=shared,
        )
        transport = AsyncTransport(
            "https://toolbox.invalid",
            client=shared,
            stream_client_factory=stream_client,
            auth=auth,
        )

        stream = await transport.open_exclusive_stream(RequestSpec("GET", "/logs"))
        try:
            assert stream.status_code == 200
            assert exchange_calls == 2
            assert seen == ["Bearer platform-1", "Bearer platform-2"]
            assert bodies[0].closed and not bodies[1].closed
        finally:
            await stream.close()
            await transport.close()


async def test_root_close_owns_active_streams_but_scope_close_does_not_cancel_siblings(monkeypatch):
    owners: list[Owner] = []

    def fresh_client(_transport: AsyncTransport) -> httpx.AsyncClient:
        owner = Owner([])
        owners.append(owner)
        return owner.client

    monkeypatch.setattr(AsyncTransport, "_new_stream_client", fresh_client)
    root = AsyncTransport("https://api.invalid")
    scope = root.scoped("https://api.invalid")
    first = await scope.open_exclusive_stream(RequestSpec("GET", "/first"))
    second = await root.open_exclusive_stream(RequestSpec("GET", "/second"))
    streams = owners[1:]
    try:
        await scope.close()
        assert all(not owner.client.is_closed for owner in owners)
        await root.close()
        assert all(owner.client.is_closed for owner in owners)
        assert all(owner.bodies[0].closed for owner in streams)
        with pytest.raises(ClientClosedError, match="closed"):
            await scope.open_exclusive_stream(RequestSpec("GET", "/after-close"))
        assert len(owners) == 3
    finally:
        await first.close()
        await second.close()
        await root.close()
