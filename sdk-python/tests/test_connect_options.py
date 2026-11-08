# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``get()``'s connect options: ``None`` is omitted, so the handle mints its
own sandbox token; a non-string, a blank toolbox URL and an access token that
breaks the header rule are ``BadRequestError`` naming the option before any
request. The TypeScript twin is ``tests/connect-options.test.ts``."""

from __future__ import annotations

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_MINTED: JsonObject = {
    "access_token": "minted-bearer",
    "expires_at": "2040-01-01T00:00:00Z",
    "token_type": "Bearer",
    "operations": [],
}


def _detail(url: str) -> JsonObject:
    return {
        "id": "sbx",
        "class": "linux",
        "name": "sbx",
        "status": "running",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        "toolbox_proxy_url": f"{url}/toolbox",
    }


def _paths(fake: FakeApi) -> list[str]:
    return [f"{request.method} {request.path.split('?')[0]}" for request in fake.requests]


async def test_none_connect_options_are_omitted_so_the_handle_mints_its_own_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with FakeApi([]) as fake:
        fake._responses.extend(
            [
                PlannedResponse(200, _detail(fake.url)),
                PlannedResponse(200, _MINTED),
                PlannedResponse(200, {"files": []}),
            ]
        )
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = await root.v2.get("sbx", toolbox_url=None, access_token=None)
            assert sandbox.access_token is None
            await sandbox.fs.list("/")
            assert sandbox.access_token == "minted-bearer"
    assert _paths(fake) == [
        "GET /sandboxes/sbx",
        "POST /sandboxes/sbx/access-token",
        "GET /toolbox/sbx/files",
    ]
    assert fake.requests[2].headers.get("Authorization") == "Bearer minted-bearer"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"toolbox_url": 5}, "toolbox_url must be a non-empty string"),
        ({"toolbox_url": " \x85 "}, "toolbox_url must be a non-empty string"),
        ({"access_token": 5}, "access_token must be a string"),
        ({"access_token": ""}, "access_token must be a non-empty string"),
        (
            {"access_token": " tok "},
            "access_token must be printable ASCII with no leading or trailing whitespace",
        ),
        (
            {"access_token": "tök"},
            "access_token must be printable ASCII with no leading or trailing whitespace",
        ),
    ],
    ids=["numeric url", "blank url", "numeric token", "empty token", "padded token", "non-ASCII token"],
)
async def test_bad_connect_options_are_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, object], message: str
) -> None:
    with FakeApi([]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError) as raised:
                await root.v2.get("sbx", **options)  # type: ignore[arg-type]
    assert (str(raised.value), raised.value.code, raised.value.source) == (message, "BAD_REQUEST", "SDK")
    assert fake.requests == []


async def test_a_valid_caller_token_is_kept_verbatim_and_never_minted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with FakeApi([]) as fake:
        fake._responses.extend([PlannedResponse(200, _detail(fake.url)), PlannedResponse(200, {"files": []})])
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = await root.v2.get("sbx", access_token="caller-token")
            await sandbox.fs.list("/")
            assert sandbox.access_token == "caller-token"
    assert _paths(fake) == ["GET /sandboxes/sbx", "GET /toolbox/sbx/files"]
    assert fake.requests[1].headers.get("Authorization") == "Bearer caller-token"
