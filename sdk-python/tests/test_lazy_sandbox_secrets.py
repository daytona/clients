# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Literal

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse
from test_core import _config_at
from typing_extensions import assert_never

from daytona.v2 import AsyncSandbox, SandboxSecretsInfo
from daytona.v2.daytona import AsyncDaytona

pytestmark = pytest.mark.anyio

SandboxSecretsAction = Literal["get", "replace"]

SECRETS_RESPONSE = PlannedResponse(
    200,
    {
        "secrets": ["OPENAI_API_KEY"],
        "desired_revision": 1,
        "applied_revision": None,
        "status": "pending",
        "error": None,
        "capture_status": None,
        "capture_unresolved_count": None,
    },
    headers={"ETag": '"sv-1"'},
)


async def _call_action(sandbox: AsyncSandbox, action: SandboxSecretsAction) -> SandboxSecretsInfo:
    match action:
        case "get":
            return await sandbox.get_secrets()
        case "replace":
            return await sandbox.replace_secrets(secrets=["OPENAI_API_KEY"], if_match='"sv-1"')
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("action", ["get", "replace"])
async def test_lazy_handle_does_not_fetch_metadata_before_a_sandbox_secrets_call(
    action: SandboxSecretsAction,
) -> None:
    with FakeApi([SECRETS_RESPONSE]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            await _call_action(lazy_sandbox(client, "lin"), action)
        finally:
            await client.close()

    assert [request.path for request in api.requests] == ["/sandboxes/lin/secrets"]
