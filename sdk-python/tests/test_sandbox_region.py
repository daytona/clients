# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
import respx
from _handles import lazy_sandbox
from test_core import _config_at, _sandbox_detail_payload

from daytona.v2._api_core import expect_sandbox_detail
from daytona.v2.daytona import AsyncDaytona


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "region_case",
    [
        ("57VSdKNrJGxnSAabjcHD4M-region-c", "", "region-c"),
        ("57VSdKNrJGxnSAabjcHD4M", "", None),
        ("57VSdKNrJGxnSAabjcHD4M-region-c", "region-a", "region-a"),
        ("550e8400-e29b-41d4-a716-446655440000", "", None),
    ],
)
async def test_region_uses_response_then_routing_tag_when_fetched(
    region_case: tuple[str, str | None, str | None],
) -> None:
    # Given a response with a region (empty or not) and an independently tagged id.
    sandbox_id, response_region, expected = region_case
    payload = _sandbox_detail_payload(sandbox_id, "running")
    payload["region_id"] = response_region
    respx.get(f"http://api.invalid/sandboxes/{sandbox_id}").respond(200, json=payload)
    async with AsyncDaytona(_config_at("http://api.invalid", api_key="key")) as client:
        # When the public operation returns a sandbox.
        sandbox = await client.get(sandbox_id)

        # Then the region comes from the response or the id's region suffix.
        assert sandbox.region == expected


@pytest.mark.anyio
@respx.mock
async def test_region_keeps_id_suffix_when_refreshed_region_is_empty() -> None:
    # Given a fetched sandbox whose next response has an empty region.
    sandbox_id = "57VSdKNrJGxnSAabjcHD4M-region-c"
    payload = _sandbox_detail_payload(sandbox_id, "running")
    payload["region_id"] = "region-c"
    route = respx.get(f"http://api.invalid/sandboxes/{sandbox_id}")
    route.respond(200, json=payload)
    async with AsyncDaytona(_config_at("http://api.invalid", api_key="key")) as client:
        sandbox = await client.get(sandbox_id)
        route.respond(200, json={**payload, "region_id": ""})
        # When metadata is refreshed.
        await sandbox.refresh_data()
        # Then an empty response region does not erase the id's region suffix.
        assert sandbox.region == "region-c"


@pytest.mark.anyio
@respx.mock
async def test_region_uses_id_suffix_when_create_selector_is_local() -> None:
    # Given a selector which is not an actual resolved region.
    respx.post("http://api.invalid/sandboxes").respond(
        200,
        json={
            "id": "57VSdKNrJGxnSAabjcHD4M-region-c",
            "status": "running",
            "name": None,
            "toolbox_proxy_url": "http://toolbox.invalid",
            "wait_cap_seconds": 0,
        },
    )
    async with AsyncDaytona(_config_at("http://api.invalid", api_key="key")) as client:
        # When create resolves the default region.
        sandbox = await client.create(region="local", wait_seconds=0)

        # Then the selector does not replace the actual region.
        assert sandbox.region == "region-c"


@pytest.mark.parametrize("region", ["", "region-a"])
def test_detail_region_uses_id_suffix_when_response_region_is_empty(region: str) -> None:
    # Given a response whose region may be empty.
    payload = _sandbox_detail_payload("57VSdKNrJGxnSAabjcHD4M-region-c", "running")
    payload["region_id"] = region
    # When the response crosses the typed parsing boundary.
    detail = expect_sandbox_detail(payload)
    # Then an explicit region wins, with the id's suffix as a fallback.
    assert detail.region_id == (region or "region-c")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sandbox_id, expected",
    [("57VSdKNrJGxnSAabjcHD4M-region-c", "region-c"), ("57VSdKNrJGxnSAabjcHD4M", None)],
)
async def test_handle_json_preserves_region_when_derived_from_id(
    sandbox_id: str,
    expected: str | None,
) -> None:
    # Given a metadata-less handle with only its routing id.
    async with AsyncDaytona(_config_at("http://api.invalid", api_key="key")) as client:
        sandbox = lazy_sandbox(client, sandbox_id)
        # When the handle is serialized.
        payload = sandbox.to_json()
        # Then a derived region survives, without inventing one for ids without a suffix.
        assert payload.get("region") == expected


@pytest.mark.parametrize("region", ["missing", None])
def test_a_detail_without_its_region_is_a_contract_error(region: str | None) -> None:
    # The contract requires region_id (a string); TypeScript refuses it too.
    from daytona.v2 import BadRequestError

    payload = _sandbox_detail_payload("57VSdKNrJGxnSAabjcHD4M-region-c", "running")
    if region == "missing":
        del payload["region_id"]
    else:
        payload["region_id"] = region
    with pytest.raises(BadRequestError, match="region_id"):
        expect_sandbox_detail(payload)
