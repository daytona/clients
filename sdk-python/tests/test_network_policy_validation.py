# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona.v2 import (
    BadRequestError,
    InternalServerError,
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
                await client.update_network_policy(
                    "sbx", network_policy={"allow_domains": None}, if_match='"np-7"'
                )
        finally:
            await client.close()

    assert len(api.requests) == 1
    assert api.requests[0].headers["If-Match"] == '"np-7"'
    assert exc_info.value.trace_id == body["trace_id"]


async def test_non_object_network_policy_response_raises_internal_server_error() -> None:
    with FakeApi([PlannedResponse(200, [])]) as api:
        client = AsyncDaytona(_config_at(api.url, api_key="key"))
        try:
            with pytest.raises(InternalServerError, match="expected JSON object"):
                await client.get_network_policy("sbx")
        finally:
            await client.close()
