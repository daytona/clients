# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sandbox access tokens: mint, refresh, retry.

Mirrors `sandbox access tokens` in `sdk-typescript/tests/unit.test.ts` (parity
contract): scoped mint requests, expiry-lead single-flight refresh,
one re-mint + one retry on 401, lazy mint for key-configured handles, the
caller-supplied-token guarantee, opaque create-response tokens, and
the ports-scoped preview-link token.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import pytest
from _handles import lazy_sandbox

from daytona.v2 import (
    AuthenticationError,
    BadRequestError,
    ForbiddenError,
    PreviewLink,
    SandboxAccessToken,
)
from daytona.v2._core import RequestSpec, parse_timestamp
from daytona.v2._transport import AsyncTransport
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.errors import create_error
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import DaytonaConfig


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    """A v2 config whose client talks to ``api_url``: the v2 client reads its
    URL from the env (the autouse conftest fixture restores it and clears
    DAYTONA_V2_API_URL)."""
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


TOOLBOX_URL = "http://toolbox.invalid/sandboxes/sbx"


def _client() -> AsyncDaytona:
    return AsyncDaytona(DaytonaConfig(api_key="key"))


def _rfc3339(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _mint_payload(
    token: str = "minted_jwt",
    *,
    expires_in: float = 3600,
    operations: Optional[list[str]] = None,
    ports: Optional[list[int]] = None,
) -> dict[str, object]:
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": _rfc3339(time.time() + expires_in),
        "operations": operations
        if operations is not None
        else ["toolbox", "ssh", "desktop", "sandbox:stop", "sandbox:pause"],
        "ports": ports,
    }


def _jwt_with_exp(exp: int) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.signature"


class _RecordingApi:
    """API transport stub: scripted responses, recorded specs."""

    token = "key"
    _source = "sdk-python-async"

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _bind_toolbox(sandbox: AsyncSandbox, handler) -> None:
    """Point the handle's scoped toolbox transport at a mock HTTP handler."""
    assert sandbox._toolbox is not None  # type: ignore[attr-defined]
    sandbox._toolbox._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler)
    )


def _recording_handler(seen: list[Optional[str]], respond=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        if respond is not None:
            return respond(request)
        return httpx.Response(200, json={"ports": []})

    return handler


@pytest.mark.anyio
async def test_create_access_token_posts_without_fetching_metadata():
    client = _client()
    # A lazy handle needs only the sandbox ID to request an access token.
    sandbox = lazy_sandbox(client, "sbx")
    api = _RecordingApi([_mint_payload("scoped_jwt", operations=["toolbox:fs", "ssh"], ports=[3000])])
    sandbox._api = api  # type: ignore[assignment]

    minted = await sandbox.create_access_token(
        operations=["toolbox:fs", "ssh"], ports=[3000], ttl_seconds=900
    )

    assert isinstance(minted, SandboxAccessToken)
    assert minted.access_token == "scoped_jwt"
    assert minted.token_type == "Bearer"
    assert minted.operations == ("toolbox:fs", "ssh")
    assert minted.ports == (3000,)
    assert [(spec.method, spec.path) for spec in api.specs] == [("POST", "/sandboxes/sbx/access-token")]
    assert api.specs[0].json_body == {
        "operations": ["toolbox:fs", "ssh"],
        "ports": [3000],
        "ttl_seconds": 900,
    }


@pytest.mark.anyio
async def test_create_access_token_full_default_mint_sends_an_empty_body():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi([_mint_payload()])
    sandbox._api = api  # type: ignore[assignment]

    minted = await sandbox.create_access_token()

    assert minted.ports is None  # null on the wire = all ports
    assert api.specs[0].json_body == {}


# The TypeScript SDK's unit.test.ts asserts the same messages for the same bodies.
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "sandbox access token response is not a JSON object"),
        ([], "sandbox access token response is not a JSON object"),
        (
            {"token_type": "Bearer", "expires_at": "2030-01-01T00:00:00Z"},
            "sandbox access token response is missing its access_token",
        ),
        (
            {
                "access_token": "",
                "token_type": "Bearer",
                "expires_at": "2030-01-01T00:00:00Z",
                "operations": [],
            },
            "sandbox access token response has an empty access_token",
        ),
        (
            {"access_token": "jwt", "token_type": "Bearer"},
            "sandbox access token response is missing its expires_at",
        ),
        (
            {"access_token": "jwt", "token_type": "Bearer", "expires_at": 5},
            "sandbox access token response has a expires_at that is not a string",
        ),
    ],
)
async def test_create_access_token_names_what_breaks_the_response_contract(payload: object, message: str):
    client = _client()
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(sandbox_class="linux", raw_sandbox_class="linux")
    sandbox._api = _RecordingApi([payload])  # type: ignore[assignment]

    with pytest.raises(BadRequestError) as caught:
        await sandbox.create_access_token()
    assert str(caught.value) == message
    assert (caught.value.status_code, caught.value.code, caught.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_expiring_token_refresh_mints_once_under_concurrency():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    sandbox._access_token = "old_jwt"
    sandbox._access_token_expires_at = datetime.fromtimestamp(
        time.time() + 30, tz=timezone.utc
    )  # inside the 60 s lead
    # A single scripted mint: a second mint would pop an empty list and fail.
    api = _RecordingApi([_mint_payload("fresh_jwt")])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    await asyncio.gather(sandbox.ports.list(), sandbox.ports.list())

    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token"]
    assert seen == ["Bearer fresh_jwt", "Bearer fresh_jwt"]
    assert sandbox.access_token == "fresh_jwt"


@pytest.mark.anyio
async def test_concurrent_callers_share_one_failed_mint_attempt():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    # Eight scripted errors: if the single-flight serialized failures, the
    # suite would see up to eight mint specs — the shared task must make
    # exactly ONE attempt and fail every joiner with it (TS parity).
    api = _RecordingApi([create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API") for _ in range(8)])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    results = await asyncio.gather(*(sandbox.ports.list() for _ in range(8)), return_exceptions=True)

    assert all(isinstance(r, Exception) for r in results)
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token"]
    assert seen == []


@pytest.mark.anyio
async def test_cancelled_joiners_leave_no_unhandled_mint_exception():
    """Every joiner cancelled + the abandoned shared mint failing must stay
    silent: the done-callback consumes the exception, and the join uses
    ``asyncio.wait`` (not ``shield``, whose 3.14+ inner handler would log the
    traceback at ERROR even with the exception consumed)."""
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    gate = asyncio.Event()

    async def failing_mint() -> str:
        await gate.wait()
        raise RuntimeError("mint boom")

    sandbox._mint_sandbox_token_now = failing_mint  # type: ignore[assignment]
    handled: list = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: handled.append(ctx))
    try:
        joiners = [asyncio.create_task(sandbox._refresh_sandbox_token(failed_token=None)) for _ in range(3)]
        await asyncio.sleep(0)  # joiners reach the shared join
        for joiner in joiners:
            joiner.cancel()
        results = await asyncio.gather(*joiners, return_exceptions=True)
        assert all(isinstance(r, asyncio.CancelledError) for r in results)
        gate.set()
        for _ in range(3):  # the abandoned mint fails; its done-callback runs
            await asyncio.sleep(0)
        assert sandbox._sandbox_token_mint is None, "done-callback cleared the shared task"
        gc.collect()  # an unretrieved exception would surface at task GC
        await asyncio.sleep(0)
        assert handled == [], f"unexpected loop exception-handler calls: {handled}"
    finally:
        loop.set_exception_handler(previous)


@pytest.mark.anyio
async def test_still_unexpired_token_rides_through_a_transient_refresh_failure():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    # Inside the 60 s refresh lead but before expiry: this token remains valid,
    # so a failed refresh must not fail the request
    # that triggered it — and the next call retries the mint (no latch).
    sandbox._access_token = "old_jwt"
    sandbox._access_token_expires_at = datetime.fromtimestamp(time.time() + 30, tz=timezone.utc)
    api = _RecordingApi(
        [
            create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"),
            create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"),
            create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"),
        ]
    )
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    result = await sandbox.ports.list()
    assert list(result.items) == []
    await sandbox.ports.list()
    assert seen == ["Bearer old_jwt", "Bearer old_jwt"]
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token", "/sandboxes/sbx/access-token"]

    # An EXPIRED token cannot be ridden — the mint error surfaces.
    sandbox._access_token_expires_at = datetime.fromtimestamp(time.time() - 1, tz=timezone.utc)
    with pytest.raises(Exception, match="boom"):
        await sandbox.ports.list()


@pytest.mark.anyio
async def test_toolbox_401_mints_once_and_retries_once():
    client = _client()
    # An opaque create-response token is reused until a 401 requires refreshing it.
    sandbox = client._sandbox_from_sandbox(
        {"id": "sbx", "status": "running"}, toolbox_url=TOOLBOX_URL, access_token="opaque_secret"
    )
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi([_mint_payload("fresh_jwt")])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer fresh_jwt":
            return httpx.Response(200, json={"ports": [3000]})
        return httpx.Response(401, json={"error": "unauthorized"})

    _bind_toolbox(sandbox, _recording_handler(seen, respond))

    result = await sandbox.ports.list()

    assert list(result.items) == [3000]
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token"]
    assert seen == ["Bearer opaque_secret", "Bearer fresh_jwt"]
    assert sandbox.access_token == "fresh_jwt"


@pytest.mark.anyio
async def test_jwt_created_handle_remints_through_the_api_transport_after_a_401():
    exchange_calls = 0
    mint_authorization: list[object] = []

    def control_handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_calls
        if request.url.path == "/auth/token":
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
        if request.url.path == "/sandboxes/sbx/access-token":
            mint_authorization.append(request.headers.get("authorization"))
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_mint_payload("fresh_jwt"),
            )
        raise AssertionError(request.url.path)

    client = AsyncDaytona(_config_at("http://api.invalid", jwt_token="jwt", project_id="project-1"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(control_handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]

    sandbox = client._sandbox_from_sandbox(
        {"id": "sbx", "status": "running"},
        toolbox_url=TOOLBOX_URL,
        access_token="opaque_secret",
    )
    sandbox._update(sandbox_class="linux")
    seen: list[Optional[str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer fresh_jwt":
            return httpx.Response(200, json={"ports": [3000]})
        return httpx.Response(401, json={"error": "unauthorized"})

    _bind_toolbox(sandbox, _recording_handler(seen, respond))

    try:
        result = await sandbox.ports.list()
    finally:
        await http.aclose()
        await client.close()

    assert sandbox._sandbox_token_mintable  # type: ignore[attr-defined]
    assert list(result.items) == [3000]
    assert exchange_calls == 1
    assert mint_authorization == ["Bearer platform-1"]
    assert seen == ["Bearer opaque_secret", "Bearer fresh_jwt"]


@pytest.mark.anyio
async def test_second_401_surfaces_after_exactly_one_remint():
    client = _client()
    sandbox = client._sandbox_from_sandbox(
        {"id": "sbx", "status": "running"}, toolbox_url=TOOLBOX_URL, access_token="opaque_secret"
    )
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi([_mint_payload("fresh_jwt")])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(
        sandbox, _recording_handler(seen, lambda request: httpx.Response(401, json={"error": "unauthorized"}))
    )

    with pytest.raises(AuthenticationError):
        await sandbox.ports.list()

    # Exactly one re-mint and one retry; the second 401 surfaces.
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token"]
    assert seen == ["Bearer opaque_secret", "Bearer fresh_jwt"]


@pytest.mark.anyio
async def test_key_configured_handle_lazily_mints_on_first_toolbox_use():
    client = _client()
    # A get()/list()-shaped handle: no token, project key configured — the
    # configuration must obtain a scoped token instead of sending the key.
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi([_mint_payload("lazy_jwt")])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    await sandbox.ports.list()
    await sandbox.ports.list()

    # One mint serves both calls; the key never reaches the toolbox.
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token"]
    assert seen == ["Bearer lazy_jwt", "Bearer lazy_jwt"]
    assert sandbox.access_token == "lazy_jwt"


@pytest.mark.anyio
async def test_caller_supplied_token_is_never_replaced():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL, access_token="caller_tok")
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi([])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(
        sandbox, _recording_handler(seen, lambda request: httpx.Response(401, json={"error": "unauthorized"}))
    )

    # Expiry surfaces to the caller: no mint, no retry, the 401 is theirs.
    with pytest.raises(AuthenticationError):
        await sandbox.ports.list()

    assert api.specs == []
    assert seen == ["Bearer caller_tok"]
    assert sandbox.access_token == "caller_tok"


@pytest.mark.anyio
async def test_create_response_opaque_token_is_used_as_is_until_401():
    client = _client()
    sandbox = client._sandbox_from_sandbox(
        {"id": "sbx", "status": "running"}, toolbox_url=TOOLBOX_URL, access_token="opaque_token"
    )
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi([])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    await sandbox.ports.list()

    # Unknown expiry, no 401: no proactive mint, the create token rides as-is.
    assert api.specs == []
    assert seen == ["Bearer opaque_token"]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [405, 501])
async def test_mint_route_unsupported_falls_back_to_the_project_key(status: int):
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    # An absent mint route (405/501) falls back to the API key without repeated mint attempts.
    api = _RecordingApi([create_error("unsupported", status, None, "DAYTONA_API")])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    await sandbox.ports.list()
    await sandbox.ports.list()

    assert len(api.specs) == 1
    assert seen == ["Bearer key", "Bearer key"]


@pytest.mark.anyio
async def test_a_mint_404_falls_back_once_and_keeps_minting():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    # A 404 is not proof the route is absent: that request falls back, the next one mints again.
    api = _RecordingApi(
        [
            create_error("not found", 404, "NOT_FOUND", "DAYTONA_API"),
            {
                "access_token": "minted",
                "expires_at": "2999-01-01T00:00:00Z",
                "token_type": "Bearer",
                "operations": [],
            },
        ]
    )
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    await sandbox.ports.list()
    await sandbox.ports.list()

    assert len(api.specs) == 2
    assert seen == ["Bearer key", "Bearer minted"]


@pytest.mark.anyio
async def test_preview_link_mints_a_ports_scoped_token_when_a_key_is_present():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    minted = _mint_payload("scoped_jwt", operations=[], ports=[3000])
    api = _RecordingApi(
        [
            {
                "id": "e1",
                "port": 3000,
                "public": False,
                "url": "https://3000-sbx.preview.example",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {"url": "https://3000-sbx.preview.example", "token": "preview-token"},
            minted,
        ]
    )
    sandbox._api = api  # type: ignore[assignment]

    link = await sandbox.ports.preview_link(3000)

    # URL unchanged; the token comes from the explicit port-scoped request.
    assert link == PreviewLink(
        url="https://3000-sbx.preview.example",
        token="scoped_jwt",
        expires_at=parse_timestamp(minted["expires_at"]),
    )
    mint = api.specs[2]
    assert (mint.method, mint.path) == ("POST", "/sandboxes/sbx/access-token")
    assert mint.json_body == {"operations": [], "ports": [3000]}


@pytest.mark.anyio
async def test_preview_link_keeps_route_token_and_advisory_expiry_when_caller_supplied():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL, access_token="caller-token")
    route_token = _jwt_with_exp(1_900_000_000)
    api = _RecordingApi(
        [
            {
                "id": "e1",
                "port": 3000,
                "public": False,
                "url": "https://3000-sbx.preview.example",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {"url": "https://3000-sbx.preview.example", "token": route_token},
        ]
    )
    sandbox._api = api  # type: ignore[assignment]

    link = await sandbox.ports.preview_link(3000)

    assert link == PreviewLink(
        url="https://3000-sbx.preview.example",
        token=route_token,
        expires_at=datetime(2030, 3, 17, 17, 46, 40, tzinfo=timezone.utc),
    )
    assert [spec.method for spec in api.specs] == ["GET", "POST"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "route_token", [_jwt_with_exp(1_900_000_000), "header.invalid.signature", "opaque-token"]
)
async def test_preview_link_mints_port_scoped_token_independently_of_route_token_contents(route_token):
    # The token's provenance and explicit requested scope determine its use,
    # never claims read from an unverified token payload.
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    minted = _mint_payload("scoped_jwt", operations=[], ports=[3000])
    api = _RecordingApi(
        [
            {
                "id": "e1",
                "port": 3000,
                "public": False,
                "url": "https://3000-sbx.preview.example",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "url": "https://3000-sbx.preview.example",
                "token": route_token,
            },
            minted,
        ]
    )
    sandbox._api = api  # type: ignore[assignment]

    link = await sandbox.ports.preview_link(3000)

    assert link == PreviewLink(
        url="https://3000-sbx.preview.example",
        token="scoped_jwt",
        expires_at=parse_timestamp(minted["expires_at"]),
    )
    assert [spec.method for spec in api.specs] == ["GET", "POST", "POST"]


@pytest.mark.anyio
async def test_preview_link_falls_back_to_the_route_token_when_the_mint_fails_transiently():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)
    sandbox._update(sandbox_class="linux")
    # The preview-url route already answered with a usable token; a transient
    # mint failure must not discard it and fail the whole call.
    api = _RecordingApi(
        [
            {
                "id": "e1",
                "port": 3000,
                "public": False,
                "url": "https://3000-sbx.preview.example",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {"url": "https://3000-sbx.preview.example", "token": "preview-token"},
            create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"),
        ]
    )
    sandbox._api = api  # type: ignore[assignment]

    link = await sandbox.ports.preview_link(3000)

    assert link == PreviewLink(url="https://3000-sbx.preview.example", token="preview-token")
    mint = api.specs[2]
    assert (mint.method, mint.path) == ("POST", "/sandboxes/sbx/access-token")


@pytest.mark.anyio
async def test_preview_link_keeps_the_route_token_for_a_caller_supplied_config():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL, access_token="caller_tok")
    sandbox._update(sandbox_class="linux")
    api = _RecordingApi(
        [
            {
                "id": "e1",
                "port": 3000,
                "public": False,
                "url": "https://3000-sbx.preview.example",
                "created_at": "2026-01-01T00:00:00Z",
            },
            {"url": "https://3000-sbx.preview.example", "token": "preview-token"},
        ]
    )
    sandbox._api = api  # type: ignore[assignment]

    link = await sandbox.ports.preview_link(3000)

    assert link == PreviewLink(url="https://3000-sbx.preview.example", token="preview-token")
    assert [spec.method for spec in api.specs] == ["GET", "POST"]


@pytest.mark.anyio
async def test_preview_link_returns_no_expiry_for_public_endpoints():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx")
    api = _RecordingApi(
        [
            {
                "id": "e1",
                "port": 3000,
                "public": True,
                "url": "https://3000-sbx.preview.example",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    sandbox._api = api  # type: ignore[assignment]

    link = await sandbox.ports.preview_link(3000)

    assert link == PreviewLink(url="https://3000-sbx.preview.example", token="", expires_at=None)
    assert [spec.method for spec in api.specs] == ["GET"]


@pytest.mark.anyio
async def test_create_wires_the_create_response_token_and_expiry_into_the_handle():
    client = _client()
    expires_at = _rfc3339(time.time() + 3600)
    sandbox_detail = {
        "class": "linux",
        "cpu": 1,
        "id": "sbx",
        "image_ref": "python:3.12",
        "memory_mib": 1024,
        "name": "sbx",
        "port_map": [],
        "region_id": "region-a",
        "disk_mib": 2048,
        "status": "running",
        "cores_max": 1,
        "lifecycle_policy": {},
        "spot": False,
        "toolbox_proxy_url": "",
    }
    responses = {
        "POST /sandboxes": {
            "id": "sbx",
            "status": "running",
            "access_token": "create_jwt",
            "access_token_expires_at": expires_at,
        },
        "GET /sandboxes/sbx": sandbox_detail,
    }

    class _CreateApi(_RecordingApi):
        def __init__(self) -> None:
            super().__init__([])

        async def send(self, spec: RequestSpec) -> object:
            self.specs.append(spec)
            return responses[f"{spec.method} {spec.path}"]

    api = _CreateApi()
    client._api = api  # type: ignore[assignment]

    sandbox = await client.create("snap")

    assert sandbox.access_token == "create_jwt"
    assert sandbox.access_token_expires_at == parse_timestamp(expires_at)
    assert sandbox._sandbox_token_mintable  # type: ignore[attr-defined]
    assert not sandbox._access_token_caller_supplied  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_mint_403_during_the_401_retry_surfaces_instead_of_the_toolbox_401():
    client = _client()
    sandbox = client._sandbox_from_sandbox(
        {"id": "sbx", "status": "running"}, toolbox_url=TOOLBOX_URL, access_token="opaque_secret"
    )
    sandbox._update(sandbox_class="linux")
    # The mint is FORBIDDEN (the credential cannot mint for this sandbox): that
    # is the actionable error, not the toolbox 401 the retry started from. The
    # unsupported-route fallback (404/405/501) is a different, kept path.
    api = _RecordingApi([create_error("forbidden", 403, "FORBIDDEN", "DAYTONA_API")])
    sandbox._api = api  # type: ignore[assignment]
    seen: list[Optional[str]] = []
    _bind_toolbox(
        sandbox, _recording_handler(seen, lambda request: httpx.Response(401, json={"error": "unauthorized"}))
    )

    with pytest.raises(ForbiddenError):
        await sandbox.ports.list()

    assert seen == ["Bearer opaque_secret"]
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx/access-token"]


@pytest.mark.anyio
async def test_refresh_data_adoption_never_gives_a_caller_supplied_handle_a_minting_provider():
    client = _client()
    # No toolbox at construction; refresh_data adopts the API's
    # toolbox_proxy_url. The adopted transport was built through a mintable
    # intermediate handle — the caller-supplied handle must strip its provider.
    sandbox = lazy_sandbox(client, "sbx", access_token="caller_tok")
    sandbox_detail = {
        "class": "linux",
        "cpu": 1,
        "id": "sbx",
        "image_ref": "python:3.12",
        "memory_mib": 1024,
        "name": "sbx",
        "port_map": [],
        "region_id": "region-a",
        "disk_mib": 2048,
        "status": "running",
        "cores_max": 1,
        "lifecycle_policy": {},
        "spot": False,
        "toolbox_proxy_url": "http://toolbox2.invalid/sandboxes",
    }
    api = _RecordingApi([sandbox_detail])
    sandbox._api = api  # type: ignore[assignment]

    await sandbox.refresh_data()

    assert sandbox._toolbox is not None  # type: ignore[attr-defined]
    assert sandbox._toolbox.auth_provider is None  # type: ignore[attr-defined]
    seen: list[Optional[str]] = []
    _bind_toolbox(
        sandbox, _recording_handler(seen, lambda request: httpx.Response(401, json={"error": "unauthorized"}))
    )

    # The caller token still rides as-is: the 401 surfaces, nothing is minted.
    with pytest.raises(AuthenticationError):
        await sandbox.ports.list()

    assert seen == ["Bearer caller_tok"]
    assert [spec.path for spec in api.specs] == ["/sandboxes/sbx"]
    assert sandbox.access_token == "caller_tok"


def test_from_api_treats_an_explicit_token_as_caller_supplied_by_default():
    # A direct from_api caller passing a scoped token, without stating whose
    # token it is, must NOT get a minting provider that would silently replace
    # it — only the SDK's own create path marks its token replaceable
    # (access_token_caller_supplied=False, the TS twin's accessTokenFromCreate).
    client = _client()
    sandbox_payload = {"id": "sbx", "status": "running"}
    toolbox, resolved, base = client._toolbox_transport("sbx", direct=TOOLBOX_URL, access_token="scoped_tok")
    pinned = AsyncSandbox._from_api(
        sandbox_payload,
        client._api,
        toolbox,
        toolbox_url=resolved,
        access_token="scoped_tok",
        toolbox_base_url=base,
        toolbox_token="scoped_tok",
    )
    assert pinned._access_token_caller_supplied  # type: ignore[attr-defined]
    assert not pinned._sandbox_token_mintable  # type: ignore[attr-defined]
    assert pinned._toolbox.auth_provider is None  # type: ignore[attr-defined]

    # Its own transport, like the real construction paths — reusing `toolbox`
    # would hand the mintable handle's provider to the transport `pinned`
    # still holds and break the guarantee this test documents.
    replaceable_box, replaceable_resolved, replaceable_base = client._toolbox_transport(
        "sbx", direct=TOOLBOX_URL, access_token="create_jwt"
    )
    replaceable = AsyncSandbox._from_api(
        sandbox_payload,
        client._api,
        replaceable_box,
        toolbox_url=replaceable_resolved,
        access_token="create_jwt",
        toolbox_base_url=replaceable_base,
        toolbox_token="create_jwt",
        access_token_caller_supplied=False,
    )
    assert not replaceable._access_token_caller_supplied  # type: ignore[attr-defined]
    assert replaceable._sandbox_token_mintable  # type: ignore[attr-defined]
    assert pinned._toolbox.auth_provider is None  # type: ignore[attr-defined]


class _TrackedBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b"payload"

    async def aclose(self) -> None:
        self.closed = True


class _StaticAuth:
    """Provider double: a fixed bearer, a recorded single re-mint."""

    def __init__(self, bearer: str, fresh: Optional[str]) -> None:
        self._bearer = bearer
        self._fresh = fresh
        self.reminted: list[Optional[str]] = []

    async def bearer(self) -> Optional[str]:
        return self._bearer

    async def on_unauthorized(self, failed_token: Optional[str]) -> Optional[str]:
        self.reminted.append(failed_token)
        return self._fresh


def _exclusive_stream_transport(
    seen: list[Optional[str]], bodies: list[_TrackedBody]
) -> tuple[AsyncTransport, httpx.AsyncClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        body = _TrackedBody()
        bodies.append(body)
        status = 200 if request.headers.get("authorization") == "Bearer fresh_jwt" else 401
        return httpx.Response(status, stream=body)

    # Returned alongside the transport: AsyncTransport.close() never closes an
    # INJECTED client (_owns_client is False), so each test closes it itself.
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncTransport("http://toolbox.invalid", client=client), client


@pytest.mark.anyio
async def test_exclusive_stream_remints_once_and_retries_once_on_401():
    seen: list[Optional[str]] = []
    bodies: list[_TrackedBody] = []
    transport, mock_client = _exclusive_stream_transport(seen, bodies)
    transport.auth_provider = _StaticAuth("stale_jwt", "fresh_jwt")

    stream = await transport.open_exclusive_stream(RequestSpec("GET", "/logs"))
    try:
        assert stream.status_code == 200
        assert seen == ["Bearer stale_jwt", "Bearer fresh_jwt"]
        assert transport.auth_provider.reminted == ["stale_jwt"]
        # The failed stream was closed before the retry.
        assert bodies[0].closed and not bodies[1].closed
    finally:
        await stream.close()
        await transport.close()
        await mock_client.aclose()


@pytest.mark.anyio
async def test_exclusive_stream_second_401_is_returned_after_exactly_one_remint():
    seen: list[Optional[str]] = []
    bodies: list[_TrackedBody] = []
    transport, mock_client = _exclusive_stream_transport(seen, bodies)
    # The re-mint hands back the failed token: no retry, the 401 stream is the
    # caller's to inspect — and on_unauthorized ran exactly once.
    transport.auth_provider = _StaticAuth("stale_jwt", "stale_jwt")

    stream = await transport.open_exclusive_stream(RequestSpec("GET", "/logs"))
    try:
        assert stream.status_code == 401
        assert seen == ["Bearer stale_jwt"]
        assert transport.auth_provider.reminted == ["stale_jwt"]
    finally:
        await stream.close()
        await transport.close()
        await mock_client.aclose()


def test_create_access_token_rejects_invalid_ports_and_ttl():
    client = _client()
    sandbox = lazy_sandbox(client, "sbx")
    sandbox._update(sandbox_class="linux")
    with pytest.raises(Exception, match="port must be an integer"):
        asyncio.run(sandbox.create_access_token(ports=[0]))
    with pytest.raises(Exception, match="ttl_seconds must be a positive integer"):
        asyncio.run(sandbox.create_access_token(ttl_seconds=0))


def _jwt_client_with_mock(handler) -> tuple[object, httpx.AsyncClient]:
    from daytona.v2.daytona import AsyncDaytona

    client = AsyncDaytona(_config_at("http://api.invalid", jwt_token="jwt", project_id="project-1"))
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api.invalid")
    client._api._client = http  # type: ignore[attr-defined]
    client._toolbox._client = http  # type: ignore[attr-defined]
    client._api._auth._client = http  # type: ignore[attr-defined]
    return client, http


@pytest.mark.anyio
async def test_jwt_handle_without_create_token_lazily_mints_on_first_toolbox_use():
    mint_calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "platform-tok",
                    "expires_in": 300,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path == "/sandboxes/sbx/access-token":
            mint_calls.append(request.headers.get("authorization"))
            return httpx.Response(200, json=_mint_payload("minted_jwt"))
        raise AssertionError(request.url.path)

    client, http = _jwt_client_with_mock(handler)
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)  # type: ignore[union-attr]
    sandbox._update(sandbox_class="linux")
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    try:
        await sandbox.ports.list()
        await sandbox.ports.list()
    finally:
        await http.aclose()
        await client.close()  # type: ignore[union-attr]

    assert seen == ["Bearer minted_jwt", "Bearer minted_jwt"]
    assert len(mint_calls) == 1
    assert mint_calls[0] == "Bearer platform-tok"


@pytest.mark.anyio
async def test_jwt_handle_mint_404_falls_back_to_project_token():
    mint_calls: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "platform-tok",
                    "expires_in": 300,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path == "/sandboxes/sbx/access-token":
            mint_calls.append(request)
            return httpx.Response(
                404, json={"message": "not found", "code": "NOT_FOUND", "source": "DAYTONA_API"}
            )
        raise AssertionError(request.url.path)

    client, http = _jwt_client_with_mock(handler)
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)  # type: ignore[union-attr]
    sandbox._update(sandbox_class="linux")
    seen: list[Optional[str]] = []
    _bind_toolbox(sandbox, _recording_handler(seen))

    try:
        await sandbox.ports.list()
        await sandbox.ports.list()
    finally:
        await http.aclose()
        await client.close()  # type: ignore[union-attr]

    # Each 404 falls back to the project token for that request only.
    assert len(mint_calls) == 2
    assert seen == ["Bearer platform-tok", "Bearer platform-tok"]


@pytest.mark.anyio
async def test_jwt_handle_without_mint_routes_falls_back_to_the_project_token():
    """No mint routes: an expired creation token is replaced by the project
    token, and a 401 on an opaque creation token retries once with it."""

    def control_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "project-tok",
                    "expires_in": 300,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path.endswith("/access-token"):
            return httpx.Response(
                404, json={"message": "not found", "code": "NOT_FOUND", "source": "DAYTONA_API"}
            )
        raise AssertionError(request.url.path)

    client, http = _jwt_client_with_mock(control_handler)
    expired = client._sandbox_from_sandbox(  # type: ignore[union-attr]
        {"id": "sbx", "status": "running"}, toolbox_url=TOOLBOX_URL, access_token="creation-tok"
    )
    expired._update(sandbox_class="linux")
    expired._access_token_expires_at = datetime.fromtimestamp(time.time() - 1, tz=timezone.utc)
    expired_seen: list[Optional[str]] = []
    _bind_toolbox(expired, _recording_handler(expired_seen))

    rejected = client._sandbox_from_sandbox(  # type: ignore[union-attr]
        {"id": "sbx2", "status": "running"}, toolbox_url=TOOLBOX_URL, access_token="opaque-creation-tok"
    )
    rejected._update(sandbox_class="linux")
    rejected_seen: list[Optional[str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer project-tok":
            return httpx.Response(200, json={"ports": []})
        return httpx.Response(401, json={"error": "unauthorized"})

    _bind_toolbox(rejected, _recording_handler(rejected_seen, respond))

    try:
        await expired.ports.list()
        await rejected.ports.list()
        await rejected.ports.list()
    finally:
        await http.aclose()
        await client.close()  # type: ignore[union-attr]

    assert expired_seen == ["Bearer project-tok"]
    # The rejected token is dropped: the next call does not repeat the 401.
    assert rejected_seen == ["Bearer opaque-creation-tok", "Bearer project-tok", "Bearer project-tok"]


@pytest.mark.anyio
async def test_jwt_handle_without_mint_routes_re_exchanges_a_rejected_project_token():
    """No mint routes, no creation token: the toolbox 401s the cached project
    token P1; the retry must re-exchange (P2) instead of surfacing the 401."""
    exchanges = 0

    def control_handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchanges
        if request.url.path == "/auth/token":
            exchanges += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"P{exchanges}",
                    "expires_in": 300,
                    "token_type": "Bearer",
                    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                },
            )
        if request.url.path.endswith("/access-token"):
            return httpx.Response(
                404, json={"message": "not found", "code": "NOT_FOUND", "source": "DAYTONA_API"}
            )
        raise AssertionError(request.url.path)

    client, http = _jwt_client_with_mock(control_handler)
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL)  # type: ignore[union-attr]
    sandbox._update(sandbox_class="linux")
    seen: list[Optional[str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer P2":
            return httpx.Response(200, json={"ports": []})
        return httpx.Response(401, json={"error": "unauthorized"})

    _bind_toolbox(sandbox, _recording_handler(seen, respond))
    try:
        await sandbox.ports.list()
    finally:
        await http.aclose()
        await client.close()  # type: ignore[union-attr]

    assert seen == ["Bearer P1", "Bearer P2"]
    assert exchanges == 2


@pytest.mark.parametrize("exp", ["100000000000000000000", "1e309", "1" + "0" * 400])
def test_preview_token_expiry_unrepresentable_exp_is_unknown_not_an_error(exp):
    from daytona.v2.ports import _preview_token_expiry

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(f'{{"exp": {exp}}}'.encode()).rstrip(b"=").decode()
    assert _preview_token_expiry(f"{header}.{payload}.sig") is None


def test_preview_token_expiry_floors_fractional_exp_to_milliseconds_like_the_typescript_sdk():
    from daytona.v2.ports import _preview_token_expiry

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    for exp, want in (
        ("1900000000.5", "2030-03-17T17:46:40.500Z"),
        ("1900000000", "2030-03-17T17:46:40Z"),
        ("1900000000.1239", "2030-03-17T17:46:40.123Z"),
    ):
        payload = base64.urlsafe_b64encode(f'{{"exp": {exp}}}'.encode()).rstrip(b"=").decode()
        assert _preview_token_expiry(f"{header}.{payload}.sig") == parse_timestamp(want)


@pytest.mark.parametrize(("depth", "want"), [(600, "2030-03-17T17:46:40Z"), (601, None)])
def test_preview_token_expiry_reads_a_payload_only_within_json_max_depth(depth, want):
    # The payload (depth 1) carries an unknown claim nested to `depth`: 600 is
    # read, 601 is not a token to read, as in TypeScript (unit.test.ts).
    from daytona.v2.ports import _preview_token_expiry

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    claims = '{"exp": 1900000000, "extra": ' + "[" * (depth - 1) + "]" * (depth - 1) + "}"
    payload = base64.urlsafe_b64encode(claims.encode()).rstrip(b"=").decode()
    expected = None if want is None else parse_timestamp(want)
    assert _preview_token_expiry(f"{header}.{payload}.sig") == expected


def test_preview_token_expiry_reads_only_the_standard_expiry_claim():
    from daytona.v2.ports import _preview_token_expiry

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()

    def tok(claims: str) -> str:
        payload = base64.urlsafe_b64encode(claims.encode()).rstrip(b"=").decode()
        return f"{header}.{payload}.sig"

    assert _preview_token_expiry(tok('{"exp": 1900000000}')) == datetime(
        2030, 3, 17, 17, 46, 40, tzinfo=timezone.utc
    )
    assert _preview_token_expiry(tok('{"exp": 1e13}')) is None
    assert _preview_token_expiry(tok('{"unrelated": "value"}')) is None
    assert _preview_token_expiry("opaque-preview-token") is None


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["toolbox_proxy_url", "toolboxProxyUrl"])
@pytest.mark.parametrize("mint", ["unsupported", "ok"])
async def test_from_api_ignores_a_toolbox_url_inside_stored_data(key: str, mint: str):
    # AsyncSandbox._from_api is internal; even so, a dict from storage must not pick
    # where the SDK sends the API key or a token it mints itself.
    response: object = (
        create_error("not found", 404, "NOT_FOUND", "DAYTONA_API")
        if mint == "unsupported"
        else _mint_payload("minted_jwt")
    )
    api = _RecordingApi([response])
    sandbox = AsyncSandbox._from_api(
        {"id": "sbx", "status": "running", "class": "linux", key: "http://evil.invalid/sandboxes"},
        api,  # type: ignore[arg-type]
        None,
    )

    assert sandbox._toolbox_proxy_url is None
    with pytest.raises(Exception, match="no toolbox URL"):
        await sandbox.ports.list()
    assert api.specs == []


@pytest.mark.anyio
async def test_from_api_honors_an_explicit_toolbox_url():
    sandbox = AsyncSandbox._from_api(
        {"id": "sbx", "status": "running", "toolbox_proxy_url": "http://evil.invalid/sandboxes"},
        None,
        None,
        toolbox_url="http://trusted.invalid/sandboxes/sbx",
    )

    assert sandbox._toolbox_proxy_url == "http://trusted.invalid/sandboxes/sbx"
