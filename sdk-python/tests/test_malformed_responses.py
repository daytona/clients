# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The malformed-response rule for the client, lifecycle and platform-service area.

A ``{}`` body is a :class:`BadRequestError` (400, ``BAD_REQUEST``, ``SDK``)
naming what is missing, and nothing follows it (no ``GET /images/None``, no
fork handle). The TypeScript twin is ``tests/malformed-responses.test.ts``.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError, ImageBuilder
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_DETAIL: JsonObject = {
    "id": "src",
    "class": "linux",
    "name": "src",
    "status": "stopped",
    "cpu": 1,
    "cores_max": 1,
    "memory_mib": 1024,
    "disk_mib": 1024,
    "port_map": [],
    "region_id": "region-a",
}

Call = Callable[[AsyncDaytonaV2], Awaitable[object]]

_CALLS: list[tuple[str, Call, str, int]] = [
    ("get", lambda d: d.get("sbx"), "id", 0),
    ("images.get", lambda d: d.images.get("img"), "id", 0),
    ("images.build", lambda d: d.images.build(ImageBuilder.from_registry("alpine")), "id", 0),
    ("images.pull", lambda d: d.images.pull("alpine", wait=False), "id", 0),
    ("snapshots.get", lambda d: d.snapshots.get("snap"), "id", 0),
    ("volumes.get", lambda d: d.volumes.get("data"), "id", 0),
    ("volumes.create", lambda d: d.volumes.create(name="data"), "id", 0),
    ("secrets.get", lambda d: d.secrets.get("KEY"), "name", 0),
    ("secrets.set", lambda d: d.secrets.set("KEY", "value", allowed_domains=[]), "name", 0),
    ("reservations.get", lambda d: d.reservations.get("res"), "id", 0),
    ("reservations.create", lambda d: d.reservations.create(region="mac"), "id", 0),
    ("reservations.create_bulk", lambda d: d.reservations.create_bulk(region="mac", count=2), "requested", 0),
    ("reservations.renew", lambda d: d.reservations.renew("res"), "id", 0),
    ("reservations.update", lambda d: d.reservations.update("res", auto_renew=False), "id", 0),
    ("mac_models.catalog", lambda d: d.mac_models.catalog(), "regions", 0),
    ("current_api_key", lambda d: d.current_api_key(), "id", 0),
    ("list_audit_actions", lambda d: d.list_audit_actions(), "targets", 0),
    ("get_limits", lambda d: d.get_limits(organization_id="org"), "sandboxes", 0),
    # The sandbox operations first read the source's class.
    ("fork", lambda d: lazy_sandbox(d, "src").fork(wait_seconds=0), "id", 1),
    ("capture_image", lambda d: lazy_sandbox(d, "src").capture_image(), "id", 0),
    ("create_snapshot", lambda d: lazy_sandbox(d, "src").create_snapshot("snap"), "id", 0),
    ("create_access_token", lambda d: lazy_sandbox(d, "src").create_access_token(), "access_token", 0),
    ("ports.expose", lambda d: lazy_sandbox(d, "src").ports.expose(8080), "port", 0),
    ("ports.signed_preview_link", lambda d: lazy_sandbox(d, "src").ports.signed_preview_link(8080), "url", 0),
]


@pytest.mark.parametrize(("name", "call", "field", "reads_first"), _CALLS, ids=[case[0] for case in _CALLS])
async def test_an_empty_body_is_a_contract_error_and_nothing_follows(
    name: str, call: Call, field: str, reads_first: int
) -> None:
    del name
    planned = [PlannedResponse(200, _DETAIL)] * reads_first + [PlannedResponse(200, {})]
    with FakeApi(planned) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError, match=r"response") as raised:
                await call(root.v2)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert len(api.requests) == reads_first + 1


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"access_token": "tok", "token_type": "Bearer", "expires_at": "tomorrow", "operations": []},
            "sandbox access token response has an expires_at that is not a timestamp",
        ),
        (
            {
                "access_token": "",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
            "sandbox access token response has an empty access_token",
        ),
        (
            {
                "access_token": "tok",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [1],
            },
            r"sandbox access token response has a operations\[0\] that is not a string",
        ),
    ],
)
async def test_a_malformed_access_token_is_a_contract_error(body: dict[str, object], message: str) -> None:
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError, match=f"^{message}$"):
                await lazy_sandbox(root.v2, "src").create_access_token()


async def test_a_detail_that_breaks_the_contract_leaves_the_list_item() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "sandboxes": [{**_DETAIL, "id": "sbx", "status": "running", "cursor": "c1"}],  # type: ignore[dict-item]
                    "next_cursor": None,
                },
            ),
            PlannedResponse(200, ["not a sandbox"]),
        ]
    ) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            page = await root.v2.list()
    assert [(sandbox.id, sandbox.state) for sandbox in page.items] == [("sbx", "started")]
    assert [request.path for request in api.requests] == ["/sandboxes", "/sandboxes/sbx"]
