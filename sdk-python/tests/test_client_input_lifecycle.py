# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import inspect
import warnings
from typing import Callable

import httpx
import pytest

from daytona import AsyncDaytona, Daytona, DaytonaConfig, _client
from daytona.v2 import ClientClosedError, NotFoundError, SandboxEventsConfig, codes
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport
from daytona.v2.types import DaytonaConfig as V2Config


@pytest.mark.parametrize("config", [DaytonaConfig, V2Config, SandboxEventsConfig])
def test_config_requires_keywords(config: Callable[..., object]) -> None:
    # Given a public config constructor, when inspecting its call contract,
    # then every configurable parameter is keyword-only.
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY for p in inspect.signature(config).parameters.values()
    )
    with pytest.raises(TypeError):
        config("value")


@pytest.mark.parametrize("status", ["404", True, 404.5, float("nan")])
def test_error_refuses_noninteger_status(status: object) -> None:
    # Given untyped caller input, when constructing an error, then refuse it.
    with pytest.raises(TypeError):
        NotFoundError("missing", status_code=status)


@pytest.mark.parametrize("factory", [Daytona, AsyncDaytona])
def test_legacy_url_warns_once(factory: Callable[[], object], monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a legacy custom endpoint and a fresh process warning latch,
    monkeypatch.setenv("DAYTONA_API_URL", "https://custom.invalid/api")
    monkeypatch.setattr(_client, "_legacy_url_warned", False)
    # When constructing roots repeatedly,
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        factory()
        factory()
    # Then warn once without leaking the endpoint.
    assert len(recorded) == 1
    assert recorded[0].category is UserWarning
    assert "custom.invalid" not in str(recorded[0].message)


@pytest.mark.parametrize(
    "value", [None, "", "  ", " HTTPS://API.DAYTONA.COM/// ", "https://APP.DAYTONA.IO/api/"]
)
def test_legacy_url_is_quiet_for_cloud_or_unset(value: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a Cloud, blank or absent legacy URL,
    monkeypatch.setattr(_client, "_legacy_url_warned", False)
    if value is None:
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
    else:
        monkeypatch.setenv("DAYTONA_API_URL", value)
    # When building the root, then no warning is emitted.
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        Daytona()
    assert recorded == []


def test_sync_stored_client_refuses_requests_after_root_close() -> None:
    # Given a stored namespace whose owner has closed,
    root = Daytona(DaytonaConfig(api_key="test-key"))
    client = root.v2
    root.close()
    # When making a new request, then report the exported closed-client error.
    with pytest.raises(ClientClosedError) as caught:
        client.list()
    assert (caught.value.code, caught.value.source, caught.value.status_code) == (
        "CLIENT_CLOSED",
        "SDK",
        None,
    )


@pytest.mark.anyio
async def test_async_stored_client_refuses_requests_after_root_close() -> None:
    # Given a stored async client whose owner has closed,
    root = AsyncDaytona(DaytonaConfig(api_key="test-key"))
    client = root.v2
    await root.close()
    # When making a new request, then report the exported closed-client error.
    with pytest.raises(ClientClosedError):
        await client.list()
    # A build-context upload is refused before it declares the upload.
    with pytest.raises(ClientClosedError):
        await client.images.build_from_context(b"FROM alpine\n")


@pytest.mark.anyio
async def test_root_reaccess_builds_a_working_client() -> None:
    # Given a root whose original namespace has closed,
    root = AsyncDaytona(DaytonaConfig(api_key="test-key"))
    previous = root.v2
    await root.close()
    fresh = root.v2
    fresh._api._client._transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"sandboxes": [], "next_cursor": None, "total": 0, "total_relation": "eq"}
        )
    )
    try:
        # When requesting through the new namespace,
        page = await fresh.list()
        # Then it works independently of the closed one.
        assert fresh is not previous
        assert page.items == ()
    finally:
        await root.close()


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["service", "toolbox", "wait"])
async def test_async_handles_refuse_requests_after_root_close(entry: str) -> None:
    # Given handles sharing their owner's transport,
    root = AsyncDaytona(DaytonaConfig(api_key="test-key"))
    client = root.v2
    client._api._client._transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "id": "sandbox-id",
                "class": "linux",
                "status": "running",
                "toolbox_proxy_url": "https://sandbox.invalid",
                "name": "test",
                "cpu": 1,
                "cores_max": 1,
                "memory_mib": 1024,
                "disk_mib": 1024,
                "region_id": "",
                "port_map": [],
            },
        )
    )
    sandbox = await client.get("sandbox-id", access_token="test-token")
    await root.close()
    # When starting a request through a service, toolbox namespace or wait,
    # then no path bypasses the closed-client error.
    with pytest.raises(ClientClosedError):
        if entry == "service":
            await client.volumes.list()
        elif entry == "toolbox":
            await sandbox.fs.read_text("/test")
        else:
            await sandbox.wait(timeout=0.01)


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["send", "request", "open_stream", "open_exclusive_stream"])
async def test_closed_scoped_transport_refuses_every_request_entry(entry: str) -> None:
    # Given a scoped transport sharing an injected pool with its closed owner,
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("request sent"))
    ) as pool:
        owner = AsyncTransport("https://api.invalid", client=pool)
        scoped = owner.scoped("https://sandbox.invalid")
        await owner.close()
        # When starting any kind of request, then refuse before the wire.
        with pytest.raises(ClientClosedError):
            if entry == "request":
                await scoped.request("GET", "/test")
            else:
                await getattr(scoped, entry)(RequestSpec("GET", "/test"))


def test_closed_error_is_known_without_fabricating_http_status() -> None:
    # Given a local closed-client error, when identifying its code,
    # then it belongs to the SDK catalog, not the HTTP status table.
    assert codes.is_known_error_code(ClientClosedError().code)
    assert "CLIENT_CLOSED" not in codes.CODE_STATUS


@pytest.mark.anyio
async def test_wait_refuses_closed_owner_when_another_client_keeps_shared_hub_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given two clients holding the same events hub,
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "1")
    first = AsyncDaytona(DaytonaConfig(api_key="test-key"))
    second = AsyncDaytona(DaytonaConfig(api_key="test-key"))
    sandbox = first.v2._sandbox_by_id("sandbox-id")
    hub = second.v2._acquire_hub()
    assert hub is sandbox._hub
    await first.close()
    try:
        # When the closed client's handle waits, then refuse before using that hub.
        with pytest.raises(ClientClosedError):
            await sandbox.wait(timeout=0.01)
    finally:
        await second.close()
