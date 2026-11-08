# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse, json_body
from test_core import _image_page_payload, _sandbox_detail_payload

from daytona.v2 import ImageBuilder, InternalServerError
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.types import DaytonaConfig

pytestmark = pytest.mark.anyio


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    """A v2 config whose client talks to ``api_url``: the v2 client reads its
    URL from the env (the autouse conftest fixture restores it and clears
    DAYTONA_V2_API_URL)."""
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


async def test_create_network_policy_preserves_missing_null_and_empty_shapes() -> None:
    responses = [
        PlannedResponse(200, {"id": "sbx-a"}),
        PlannedResponse(200, _sandbox_detail_payload("sbx-a", "running")),
        PlannedResponse(200, {"id": "sbx-b"}),
        PlannedResponse(200, _sandbox_detail_payload("sbx-b", "running")),
        PlannedResponse(200, {"id": "sbx-c"}),
        PlannedResponse(200, _sandbox_detail_payload("sbx-c", "running")),
        PlannedResponse(200, {"id": "sbx-d"}),
        PlannedResponse(200, _sandbox_detail_payload("sbx-d", "running")),
        PlannedResponse(200, {"id": "sbx-e"}),
        PlannedResponse(200, _sandbox_detail_payload("sbx-e", "running")),
    ]
    with FakeApi(responses) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            await client.create("python:3.12")
            await client.create("python:3.12", network_policy=None)
            await client.create("python:3.12", network_policy={})
            await client.create("python:3.12", network_policy={"allow_domains": None})
            await client.create("python:3.12", network_policy={"allow_domains": []})
        finally:
            await client.close()

    post_bodies = [json_body(request) for request in api.requests if request.method == "POST"]
    assert "network_policy" not in post_bodies[0]
    assert post_bodies[1]["network_policy"] is None
    assert post_bodies[2]["network_policy"] == {}
    assert post_bodies[3]["network_policy"] == {"allow_domains": None}
    assert post_bodies[4]["network_policy"] == {"allow_domains": []}


async def test_get_network_policy_returns_etag_and_read_side_versions() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "desired": {
                        "version": 2,
                        "block_all": True,
                        "allow_cidrs": ["1.2.3.4/32"],
                        "allow_domains": ["api.example.com"],
                    },
                    "desired_revision": 7,
                    "applied": {"version": 2, "block_all": False, "allow_cidrs": [], "allow_domains": None},
                    "applied_revision": 6,
                    "applied_start_id": "start-1",
                    "status": "pending",
                    "error": None,
                },
                headers={"ETag": '"np-7"'},
            )
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            policy = await client.get_network_policy("sbx")
        finally:
            await client.close()

    assert api.requests[0].path == "/sandboxes/sbx/network-policy"
    assert policy.etag == '"np-7"'
    assert policy.http_status == 200
    assert policy.desired is not None and policy.desired.version == 2
    assert policy.applied is not None and policy.applied.version == 2
    assert policy.applied_start_id == "start-1"
    assert policy.status == "pending"


async def test_update_and_reset_network_policy_use_exact_path_headers_and_actual_202_status() -> None:
    with FakeApi(
        [
            PlannedResponse(
                202,
                {
                    "desired": {"version": 2, "block_all": True, "allow_cidrs": [], "allow_domains": []},
                    "desired_revision": 2,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "pending",
                    "error": None,
                },
                headers={"ETag": '"np-2"'},
            ),
            PlannedResponse(
                202,
                {
                    "desired": None,
                    "desired_revision": 3,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "applied",
                    "error": None,
                },
                headers={"ETag": '"np-3"'},
            ),
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            updated = await client.update_network_policy(
                "sbx",
                network_policy={"block_all": True, "allow_cidrs": [], "allow_domains": []},
                if_match='"np-1"',
            )
            reset = await client.reset_network_policy("sbx", if_match='"np-2"')
        finally:
            await client.close()

    assert updated.http_status == 202
    assert updated.etag == '"np-2"'
    assert reset.http_status == 202
    assert reset.etag == '"np-3"'
    assert api.requests[0].path == "/sandboxes/sbx/network-policy"
    assert api.requests[0].headers["If-Match"] == '"np-1"'
    assert json_body(api.requests[0]) == {
        "block_all": True,
        "allow_cidrs": [],
        "allow_domains": [],
    }
    assert api.requests[1].headers["If-Match"] == '"np-2"'
    assert json_body(api.requests[1]) is None


async def test_network_policy_routes_percent_encode_lazy_sandbox_ids() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "desired": None,
                    "desired_revision": 1,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "applied",
                    "error": None,
                },
                headers={"ETag": '"np-1"'},
            ),
            PlannedResponse(
                202,
                {
                    "desired": {"version": 2, "block_all": True, "allow_cidrs": [], "allow_domains": []},
                    "desired_revision": 2,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "pending",
                    "error": None,
                },
                headers={"ETag": '"np-2"'},
            ),
            PlannedResponse(
                202,
                {
                    "desired": None,
                    "desired_revision": 3,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "applied",
                    "error": None,
                },
                headers={"ETag": '"np-3"'},
            ),
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        sandbox = lazy_sandbox(client, "seg/ment")
        try:
            await sandbox.get_network_policy()
            await sandbox.update_network_policy(
                network_policy={"block_all": True, "allow_cidrs": [], "allow_domains": []},
                if_match='"np-1"',
            )
            await sandbox.reset_network_policy(if_match='"np-2"')
        finally:
            await client.close()

    assert [request.path for request in api.requests] == [
        "/sandboxes/seg%2Fment/network-policy",
        "/sandboxes/seg%2Fment/network-policy",
        "/sandboxes/seg%2Fment/network-policy",
    ]


async def test_linux_snapshot_restore_omits_network_policy_and_build_sends_it() -> None:
    responses = [
        PlannedResponse(200, {"id": "sbx-restore", "status": "running", "wait_cap_seconds": 3}),
        PlannedResponse(
            200,
            {"id": "img-built", "status": "pending"},
        ),
        PlannedResponse(200, _image_page_payload("img-built")),
        PlannedResponse(200, {"id": "sbx-build", "status": "running", "wait_cap_seconds": 3}),
    ]
    with FakeApi(responses) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            await client.create_from_snapshot("snap_1")
            await client.create_from_build(
                ImageBuilder.from_dockerfile("FROM python:3.12\n"),
                network_policy={"allow_domains": ["api.example.com"]},
            )
        finally:
            await client.close()

    post_requests = [request for request in api.requests if request.method == "POST"]
    assert post_requests[0].path == "/sandboxes/from-snapshot"
    assert "network_policy" not in json_body(post_requests[0])
    assert post_requests[2].path == "/sandboxes"
    assert json_body(post_requests[2])["network_policy"] == {"allow_domains": ["api.example.com"]}


async def test_network_policy_response_parser_accepts_non_api_200_compat_success_shape() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "desired": None,
                    "desired_revision": 4,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "applied",
                    "error": None,
                },
                headers={"ETag": '"np-4"'},
            )
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            policy = await client.get_network_policy("sbx")
        finally:
            await client.close()

    assert policy.http_status == 200
    assert policy.etag == '"np-4"'


async def test_malformed_server_network_policy_arrays_raise_internal_server_error() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "desired": {"version": 2, "block_all": True, "allow_cidrs": [1], "allow_domains": None},
                    "desired_revision": 7,
                    "applied": None,
                    "applied_revision": None,
                    "applied_start_id": None,
                    "status": "pending",
                    "error": None,
                },
                headers={"ETag": '"np-7"'},
            )
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(InternalServerError, match="desired.allow_cidrs must be an array of strings"):
                await client.get_network_policy("sbx")
        finally:
            await client.close()
