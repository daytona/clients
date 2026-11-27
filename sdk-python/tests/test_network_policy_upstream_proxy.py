# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Shared with the TypeScript SDK's networkPolicyUpstreamProxy.test.ts.

from __future__ import annotations

import os

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse, json_body
from test_core import _sandbox_detail_payload

from daytona.v2 import BadRequestError, NetworkPolicy, NetworkPolicyInput, UpstreamProxy
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.network_policy import serialize_network_policy_input
from daytona.v2.types import DaytonaConfig


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


@pytest.mark.parametrize(
    ("policy", "body"),
    [
        (
            {"upstream_proxy": {"url": "http://proxy.example.com:3128"}},
            {"upstream_proxy": {"url": "http://proxy.example.com:3128"}},
        ),
        (
            {"upstream_proxy": {"url": "https://proxy.example.com", "credentials_secret": "PROXY_AUTH"}},
            {"upstream_proxy": {"url": "https://proxy.example.com", "credentials_secret": "PROXY_AUTH"}},
        ),
        (
            {"upstream_proxy": {"url": "http://proxy.example.com", "credentials_secret": None}},
            {"upstream_proxy": {"url": "http://proxy.example.com", "credentials_secret": None}},
        ),
        ({"upstream_proxy": None}, {"upstream_proxy": None}),
        ({}, {}),
        (
            {
                "block_all": True,
                "allow_domains": ["pypi.org"],
                "upstream_proxy": {"url": "http://proxy.example.com"},
            },
            {
                "block_all": True,
                "allow_domains": ["pypi.org"],
                "upstream_proxy": {"url": "http://proxy.example.com"},
            },
        ),
    ],
)
def test_upstream_proxy_is_serialized(policy: dict[str, object], body: dict[str, object]) -> None:
    assert serialize_network_policy_input(policy) == body  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"upstream_proxy": "http://proxy.example.com"}, "network_policy.upstream_proxy must be an object"),
        ({"upstream_proxy": []}, "network_policy.upstream_proxy must be an object"),
        ({"upstream_proxy": {}}, "network_policy.upstream_proxy.url must be a string"),
        ({"upstream_proxy": {"url": 3128}}, "network_policy.upstream_proxy.url must be a string"),
        (
            {"upstream_proxy": {"url": "http://p.example", "credentials_secret": 1}},
            "network_policy.upstream_proxy.credentials_secret must be a string",
        ),
        (
            {"upstream_proxy": {"url": "http://p.example", "credentialsSecret": "S"}},
            "network_policy.upstream_proxy contains unknown fields: credentialsSecret",
        ),
    ],
)
def test_a_malformed_upstream_proxy_is_refused_locally(policy: dict[str, object], message: str) -> None:
    with pytest.raises(BadRequestError, match=f"^{message}$") as raised:
        serialize_network_policy_input(policy)  # type: ignore[arg-type]
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_upstream_proxy_is_sent_on_create_and_update() -> None:
    policy_response = PlannedResponse(
        202,
        {
            "desired": {
                "version": 3,
                "block_all": False,
                "allow_cidrs": [],
                "allow_domains": None,
                "upstream_proxy": {
                    "url": "https://proxy.example.com:443",
                    "credentials_secret": "PROXY_AUTH",
                },
            },
            "desired_revision": 2,
            "applied": None,
            "status": "pending",
        },
        headers={"ETag": '"np-2"'},
    )
    network_policy: NetworkPolicyInput = {
        "upstream_proxy": {"url": "https://proxy.example.com", "credentials_secret": "PROXY_AUTH"}
    }
    with FakeApi(
        [
            PlannedResponse(200, {"id": "sbx-a", "status": "pending"}),
            PlannedResponse(200, _sandbox_detail_payload("sbx-a", "running")),
            policy_response,
        ]
    ) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            await client.create("python:3.12", network_policy=network_policy)
            updated = await lazy_sandbox(client, "sbx").update_network_policy(
                network_policy=network_policy, if_match='"np-1"'
            )
        finally:
            await client.close()

    wire = {"upstream_proxy": {"url": "https://proxy.example.com", "credentials_secret": "PROXY_AUTH"}}
    assert json_body(api.requests[0])["network_policy"] == wire
    assert api.requests[2].path == "/sandboxes/sbx/network-policy"
    assert json_body(api.requests[2]) == wire
    assert updated.desired == NetworkPolicy(
        version=3,
        block_all=False,
        allow_cidrs=(),
        allow_domains=None,
        upstream_proxy=UpstreamProxy(url="https://proxy.example.com:443", credentials_secret="PROXY_AUTH"),
    )


@pytest.mark.anyio
async def test_a_proxy_without_credentials_and_a_policy_without_a_proxy_are_read() -> None:
    body = {
        "desired": {
            "version": 3,
            "block_all": False,
            "allow_cidrs": [],
            "upstream_proxy": {"url": "http://proxy.example.com:80", "credentials_secret": None},
        },
        "desired_revision": 5,
        "applied": {"version": 2, "block_all": False, "allow_cidrs": [], "upstream_proxy": None},
        "applied_revision": 4,
    }
    with FakeApi([PlannedResponse(200, body, headers={"ETag": '"np-5"'})]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            policy = await lazy_sandbox(client, "sbx").get_network_policy()
        finally:
            await client.close()

    assert policy.desired is not None
    assert policy.desired.upstream_proxy == UpstreamProxy(url="http://proxy.example.com:80")
    assert policy.desired.upstream_proxy.credentials_secret is None
    assert policy.applied is not None and policy.applied.upstream_proxy is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("upstream_proxy", "message"),
    [
        ({"url": 1}, "desired.upstream_proxy.url must be a string"),
        (
            {"url": "http://p.example:80", "credentials_secret": 7},
            "desired.upstream_proxy.credentials_secret must be a string",
        ),
        ("http://p.example:80", "desired.upstream_proxy must be an object"),
    ],
)
async def test_a_malformed_response_proxy_is_a_contract_error(upstream_proxy: object, message: str) -> None:
    body = {
        "desired": {"version": 3, "block_all": False, "allow_cidrs": [], "upstream_proxy": upstream_proxy},
        "desired_revision": 6,
    }
    with FakeApi([PlannedResponse(200, body, headers={"ETag": '"np-6"'})]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(BadRequestError, match=f"^{message}$") as raised:
                await lazy_sandbox(client, "sbx").get_network_policy()
        finally:
            await client.close()
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
