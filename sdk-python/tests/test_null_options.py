# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``None`` on every create and fork option: omitted, refused (``wait_seconds``)
or, for the network policy, an explicit null. The TypeScript twin table is
``tests/null-options.test.ts``, where ``null`` does exactly the same."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
from daytona.v2.types import JsonObject

pytestmark = pytest.mark.anyio

_CREATED: JsonObject = {"id": "sbx", "status": "running"}
_ROUTES: dict[str, Callable[[AsyncDaytonaV2, dict[str, object]], Awaitable[object]]] = {
    "create": lambda d, o: d.create("img_1", **{"wait_seconds": 0, **o}),  # type: ignore[arg-type]
    "create_from_snapshot": lambda d, o: d.create_from_snapshot("snap_1", **{"wait_seconds": 0, **o}),  # type: ignore[arg-type]
    "create_macos": lambda d, o: d.create_macos("sonoma", **{"wait_seconds": 0, **o}),  # type: ignore[arg-type]
}
_OMITTED = {
    "create": [
        "name", "labels", "ports", "lifecycle_policy", "region", "create_timeout_seconds", "idempotency_key",
        "volumes", "env", "resources", "secrets", "spot", "user", "cpu_arch", "cpu_type",
    ],
    "create_from_snapshot": [
        "name", "labels", "ports", "lifecycle_policy", "region", "create_timeout_seconds", "idempotency_key",
    ],
    "create_macos": [
        "name", "labels", "ports", "lifecycle_policy", "region", "create_timeout_seconds", "idempotency_key",
        "volumes", "env", "secrets", "resources", "persistent", "reservation", "auto_renew",
    ],
}  # fmt: skip


async def _body(route: str, options: dict[str, object]) -> JsonObject:
    with FakeApi([PlannedResponse(200, _CREATED)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await _ROUTES[route](root.v2, options)
    sent = json.loads(api.requests[0].body)
    assert isinstance(sent, dict)
    # A random idempotency key differs per call; the rest must match.
    sent.pop("idempotency_key", None)
    return sent


@pytest.mark.parametrize(
    ("route", "key"),
    [(route, key) for route, keys in _OMITTED.items() for key in keys],
    ids=lambda value: str(value),
)
async def test_none_is_omitted(route: str, key: str) -> None:
    assert await _body(route, {key: None}) == await _body(route, {})


@pytest.mark.parametrize("route", list(_ROUTES))
async def test_wait_seconds_none_is_refused(route: str) -> None:
    with FakeApi([]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError):
                await _ROUTES[route](root.v2, {"wait_seconds": None})
    assert api.requests == []


@pytest.mark.parametrize("route", ["create", "create_macos"])
async def test_network_policy_none_is_an_explicit_null(route: str) -> None:
    assert (await _body(route, {"network_policy": None}))["network_policy"] is None


async def test_fork_none_name_and_memory_are_omitted_and_wait_none_refused() -> None:
    fork: JsonObject = {
        "id": "f",
        "status": "pending",
        "forked_from": "src",
        "toolbox_proxy_url": "https://p.example",
    }
    detail: JsonObject = {"id": "src", "class": "linux", "name": "src", "status": "stopped", "cpu": 1,
                          "cores_max": 1, "memory_mib": 1024, "disk_mib": 1024, "port_map": [], "region_id": "r"}  # fmt: skip
    with FakeApi([PlannedResponse(200, detail), PlannedResponse(200, fork)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            source = lazy_sandbox(root.v2, "src")
            await source.fork(name=None, memory=None, wait_seconds=0)
            with pytest.raises(BadRequestError):
                await source.fork(wait_seconds=None)  # type: ignore[arg-type]
    assert json.loads(api.requests[1].body) == {}
    assert len(api.requests) == 2
