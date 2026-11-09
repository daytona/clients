# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A handle of a JWT client (``jwt_token`` + ``project_id``) mints its own
sandbox token, as one with an API key does: its toolbox calls send the minted
bearer, never the project token. A mint route answering 405 falls back to the
project token, and a caller-supplied ``access_token`` is never minted over.
The TypeScript twin is ``tests/jwt-handle-mint.test.ts``."""

from __future__ import annotations

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_EXCHANGE: JsonObject = {
    "access_token": "project-token",
    "expires_in": 300,
    "token_type": "Bearer",
    "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
}
_MINTED: JsonObject = {
    "access_token": "minted-bearer",
    "expires_at": "2040-01-01T00:00:00Z",
    "token_type": "Bearer",
    "operations": ["toolbox"],
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


def _sent(fake: FakeApi) -> list[tuple[str, str, str | None]]:
    return [(r.method, r.path.split("?")[0], r.headers.get("Authorization")) for r in fake.requests]


async def _list_files(
    monkeypatch: pytest.MonkeyPatch, answers: list[PlannedResponse], access_token: str | None = None
) -> FakeApi:
    with FakeApi([]) as fake:
        fake._responses.extend(
            [PlannedResponse(200, _EXCHANGE), PlannedResponse(200, _detail(fake.url)), *answers]
        )
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(jwt_token="jwt", project_id="project-1")) as root:
            sandbox = await root.v2.get("sbx", access_token=access_token)
            await sandbox.fs.list("/")
    return fake


async def test_a_jwt_handle_mints_and_sends_the_minted_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = await _list_files(
        monkeypatch, [PlannedResponse(200, _MINTED), PlannedResponse(200, {"items": []})]
    )
    assert _sent(fake)[1:] == [
        ("GET", "/sandboxes/sbx", "Bearer project-token"),
        ("POST", "/sandboxes/sbx/access-token", "Bearer project-token"),
        ("GET", "/toolbox/sbx/files", "Bearer minted-bearer"),
    ]


async def test_a_jwt_handle_whose_mint_route_answers_405_falls_back_to_the_project_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = await _list_files(
        monkeypatch, [PlannedResponse(405, {"message": "no mint"}), PlannedResponse(200, {"items": []})]
    )
    assert _sent(fake)[2:] == [
        ("POST", "/sandboxes/sbx/access-token", "Bearer project-token"),
        ("GET", "/toolbox/sbx/files", "Bearer project-token"),
    ]


async def test_a_jwt_handle_sends_a_caller_token_unchanged_and_never_mints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = await _list_files(monkeypatch, [PlannedResponse(200, {"items": []})], access_token="caller-token")
    assert _sent(fake)[1:] == [
        ("GET", "/sandboxes/sbx", "Bearer project-token"),
        ("GET", "/toolbox/sbx/files", "Bearer caller-token"),
    ]
