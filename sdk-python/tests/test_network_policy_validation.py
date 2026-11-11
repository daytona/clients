# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona.v2 import (
    BadRequestError,
    PreconditionFailedError,
    PreconditionRequiredError,
    ServiceUnavailableError,
)
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.network_policy import serialize_network_policy_input
from daytona.v2.types import DaytonaConfig

pytestmark = pytest.mark.anyio


def _config_at(api_url: str, **kwargs: object) -> DaytonaConfig:
    """A v2 config whose client talks to ``api_url``: the v2 client reads its
    URL from the env (the autouse conftest fixture restores it and clears
    DAYTONA_V2_API_URL)."""
    os.environ["DAYTONA_V2_API_URL"] = api_url
    return DaytonaConfig(**kwargs)


async def test_create_network_policy_rejects_unknown_fields_and_read_side_version() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    try:
        with pytest.raises(BadRequestError, match="network_policy contains unknown fields: version"):
            await client.create("python:3.12", network_policy={"version": 2})
        with pytest.raises(BadRequestError, match="network_policy contains unknown fields: extra"):
            await client.create("python:3.12", network_policy={"extra": True})
    finally:
        await client.close()


def test_domains_default_block_all_true() -> None:
    assert serialize_network_policy_input({"allow_domains": ["api.example.com"]}) == {
        "allow_domains": ["api.example.com"]
    }


# Shared with the TypeScript SDK's networkPolicyValidation.test.ts: an open
# policy ignores allow_cidrs, so a CIDR list without block_all True or a domain
# list is refused rather than creating an open sandbox.
@pytest.mark.parametrize(
    "policy",
    [
        {"allow_cidrs": ["10.0.0.0/8"]},
        {"allow_cidrs": ["10.0.0.0/8"], "block_all": False},
        {"allow_cidrs": ["10.0.0.0/8"], "block_all": None},
        {"allow_cidrs": ["10.0.0.0/8"], "allow_domains": None},
    ],
)
def test_an_open_cidr_only_policy_is_refused(policy: dict[str, object]) -> None:
    with pytest.raises(
        BadRequestError,
        match=r"^network_policy allow_cidrs restricts egress only with block_all set to true or with "
        r"allow_domains; without them the policy is open$",
    ) as raised:
        serialize_network_policy_input(policy)  # type: ignore[arg-type]
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.parametrize(
    ("policy", "body"),
    [
        (
            {"allow_cidrs": ["10.0.0.0/8"], "block_all": True},
            {"allow_cidrs": ["10.0.0.0/8"], "block_all": True},
        ),
        (
            {"allow_cidrs": ["10.0.0.0/8"], "allow_domains": ["a.example"]},
            {"allow_cidrs": ["10.0.0.0/8"], "allow_domains": ["a.example"]},
        ),
        (
            {"allow_cidrs": ["10.0.0.0/8"], "allow_domains": []},
            {"allow_cidrs": ["10.0.0.0/8"], "allow_domains": []},
        ),
        ({"allow_cidrs": []}, {"allow_cidrs": []}),
    ],
)
def test_a_restricting_cidr_policy_is_sent(policy: dict[str, object], body: dict[str, object]) -> None:
    assert serialize_network_policy_input(policy) == body  # type: ignore[arg-type]


def test_block_all_none_is_omitted() -> None:
    assert serialize_network_policy_input({"allow_domains": ["api.example.com"], "block_all": None}) == {
        "allow_domains": ["api.example.com"]
    }


async def test_domains_with_block_all_false_rejected() -> None:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    try:
        with pytest.raises(BadRequestError) as excinfo:
            await client.create(
                "python:3.12",
                network_policy={"allow_domains": ["api.example.com"], "block_all": False},
            )
        assert excinfo.value.code == "NETWORK_POLICY_DOMAINS_REQUIRE_BLOCK_ALL"
        assert excinfo.value.status_code == 400
    finally:
        await client.close()


@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (412, {"code": "STALE_NETWORK_POLICY_REVISION", "trace_id": "trace-stale"}, PreconditionFailedError),
        (428, {"code": "IF_MATCH_REQUIRED", "trace_id": "trace-precondition"}, PreconditionRequiredError),
        (
            503,
            {"code": "SERVICE_UNAVAILABLE", "trace_id": "trace-unavailable"},
            ServiceUnavailableError,
        ),
    ],
)
async def test_network_policy_errors_preserve_trace_id_without_auto_retry(
    status: int, body: object, error_type: type[Exception]
) -> None:
    with FakeApi([PlannedResponse(status, body)]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(error_type) as exc_info:
                await lazy_sandbox(client, "sbx").update_network_policy(
                    network_policy={"allow_domains": None}, if_match='"np-7"'
                )
        finally:
            await client.close()

    assert len(api.requests) == 1
    assert api.requests[0].headers["If-Match"] == '"np-7"'
    assert exc_info.value.trace_id == body["trace_id"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([], "network policy response must be an object"),
        ({"desired_revision": 1, "applied": []}, "applied must be an object"),
        ({"desired_revision": 1, "desired": "x"}, "desired must be an object"),
    ],
)
async def test_a_non_object_network_policy_response_is_a_contract_error(body: object, message: str) -> None:
    # The message names the field, as TypeScript's does.
    with FakeApi([PlannedResponse(200, body, {"ETag": '"np-1"'})]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(BadRequestError, match=f"^{message}$") as raised:
                await lazy_sandbox(client, "sbx").get_network_policy()
        finally:
            await client.close()
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_a_network_policy_response_without_etag_is_a_contract_error() -> None:
    body = {
        "desired": None,
        "desired_revision": 1,
        "applied": None,
        "applied_revision": None,
        "applied_start_id": None,
        "status": "applied",
        "error": None,
    }
    with FakeApi([PlannedResponse(200, body)]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(BadRequestError) as raised:
                await lazy_sandbox(client, "sbx").get_network_policy()
        finally:
            await client.close()
    assert (raised.value.status_code, raised.value.code, raised.value.source, raised.value.message) == (
        400,
        "BAD_REQUEST",
        "SDK",
        "network_policy response is missing the ETag header",
    )
