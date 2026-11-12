# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse, json_body
from test_core import _config_at

from daytona.v2 import BadRequestError, PreconditionFailedError
from daytona.v2.daytona import AsyncDaytona

pytestmark = pytest.mark.anyio


async def test_get_sandbox_secrets_returns_etag_and_runtime_fields() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "secrets": ["OPENAI_API_KEY", "GITHUB_TOKEN"],
                    "desired_revision": 7,
                    "applied_revision": 6,
                    "status": "pending",
                    "error": None,
                    "capture_status": None,
                    "capture_unresolved_count": None,
                },
                headers={"ETag": '"sv-7"'},
            )
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            info = await lazy_sandbox(client, "sbx").get_secrets()
        finally:
            await client.close()

    assert api.requests[0].path == "/sandboxes/sbx/secrets"
    assert info.etag == '"sv-7"'
    assert info.secrets == ("OPENAI_API_KEY", "GITHUB_TOKEN")
    assert info.applied_revision == 6
    assert info.status == "pending"


async def test_replace_sandbox_secrets_uses_exact_path_headers_and_202_status() -> None:
    with FakeApi(
        [
            PlannedResponse(
                202,
                {
                    "secrets": ["OPENAI_API_KEY"],
                    "desired_revision": 2,
                    "applied_revision": None,
                    "status": "pending",
                    "error": None,
                    "capture_status": None,
                    "capture_unresolved_count": None,
                },
                headers={"ETag": '"sv-2"'},
            )
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            updated = await lazy_sandbox(client, "sbx").replace_secrets(
                secrets=["OPENAI_API_KEY"],
                if_match='"sv-1"',
            )
        finally:
            await client.close()

    assert updated.etag == '"sv-2"'
    assert api.requests[0].path == "/sandboxes/sbx/secrets"
    assert api.requests[0].headers["If-Match"] == '"sv-1"'
    assert json_body(api.requests[0]) == {"secrets": ["OPENAI_API_KEY"]}


async def test_sandbox_secret_routes_percent_encode_lazy_sandbox_ids_and_preserve_trace_ids() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "secrets": [],
                    "desired_revision": 1,
                    "applied_revision": None,
                    "status": None,
                    "error": None,
                    "capture_status": None,
                    "capture_unresolved_count": None,
                },
                headers={"ETag": '"sv-1"'},
            ),
            PlannedResponse(412, {"code": "STALE_SECRETS_REVISION", "trace_id": "trace-secret-stale"}),
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        sandbox = lazy_sandbox(client, "seg/ment")
        try:
            await sandbox.get_secrets()
            with pytest.raises(PreconditionFailedError) as exc:
                await sandbox.replace_secrets(secrets=[], if_match='"sv-9"')
            assert exc.value.trace_id == "trace-secret-stale"
        finally:
            await client.close()

    assert [request.path for request in api.requests] == [
        "/sandboxes/seg%2Fment/secrets",
        "/sandboxes/seg%2Fment/secrets",
    ]


async def test_replace_sandbox_secrets_rejects_plain_objects_before_any_request() -> None:
    with FakeApi([]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(BadRequestError, match="sandbox secrets must be an array of strings"):
                await lazy_sandbox(client, "sbx").replace_secrets(
                    secrets={"NAME": "value"},
                    if_match='"sv-1"',
                )
        finally:
            await client.close()

    assert api.requests == []
