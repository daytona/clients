# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from typing import Literal

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse
from typing_extensions import assert_never

from daytona.v2 import AsyncSandbox, NetworkPolicyInfo
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.types import DaytonaConfig

pytestmark = pytest.mark.anyio

NetworkPolicyAction = Literal["get", "update", "reset"]


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    """A v2 config whose client talks to ``api_url``: the v2 client reads its
    URL from the env (the autouse conftest fixture restores it and clears
    DAYTONA_V2_API_URL)."""
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


POLICY_RESPONSE = PlannedResponse(
    200,
    {
        "desired": None,
        "desired_revision": 1,
        "applied": None,
        "applied_revision": None,
        "applied_start_id": None,
        "state": "applied",
        "error": None,
    },
    headers={"ETag": '"np-1"'},
)


async def _call_network_policy_action(
    sandbox: AsyncSandbox, action: NetworkPolicyAction
) -> NetworkPolicyInfo:
    match action:
        case "get":
            return await sandbox.get_network_policy()
        case "update":
            return await sandbox.update_network_policy(
                network_policy={"block_all": True, "allow_cidrs": [], "allow_domains": []},
                if_match='"np-1"',
            )
        case "reset":
            return await sandbox.reset_network_policy(if_match='"np-1"')
        case unreachable:
            assert_never(unreachable)


@pytest.mark.parametrize("action", ["get", "update", "reset"])
async def test_lazy_handle_does_not_fetch_metadata_before_a_network_policy_call(
    action: NetworkPolicyAction,
) -> None:
    with FakeApi([POLICY_RESPONSE]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        sandbox = lazy_sandbox(client, "lin")
        try:
            await _call_network_policy_action(sandbox, action)
        finally:
            await client.close()

    assert [request.path for request in api.requests] == ["/sandboxes/lin/network-policy"]
    assert sandbox.sandbox_class is None
