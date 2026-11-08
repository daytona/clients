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
from daytona.v2 import BadRequestError, MacosResources, PortMapping, Resources, VolumeMount
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
    with FakeApi([PlannedResponse(200, fork)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            source = lazy_sandbox(root.v2, "src")
            await source.fork(name=None, memory=None, wait_seconds=0)
            with pytest.raises(BadRequestError):
                await source.fork(wait_seconds=None)  # type: ignore[arg-type]
    assert json.loads(api.requests[0].body) == {}
    assert len(api.requests) == 1


# The fields of nested option objects: the TypeScript twin table pins the same rows.
_NESTED: list[tuple[str, str, dict[str, object], dict[str, object]]] = [
    (
        "create",
        "resources.cpu_max",
        {"resources": Resources(cpu=2, cpu_max=None)},
        {"resources": Resources(cpu=2)},
    ),
    (
        "create",
        "resources.memory",
        {"resources": Resources(memory=None, disk=10)},
        {"resources": Resources(disk=10)},
    ),
    ("create", "resources (all None)", {"resources": Resources()}, {}),
    (
        "create",
        "volumes[].subpath and read_only",
        {"volumes": [VolumeMount(volume_id="v", mount_path="/m", subpath=None, read_only=None)]},
        {"volumes": [VolumeMount(volume_id="v", mount_path="/m")]},
    ),
    (
        "create_macos",
        "resources.cpu",
        {"resources": MacosResources(cpu=None, memory=8)},
        {"resources": MacosResources(memory=8)},
    ),
    ("create", "lifecycle_policy.timeout_seconds", {"lifecycle_policy": {"timeout_seconds": None}}, {}),
    (
        "create",
        "network_policy.block_all",
        {"network_policy": {"block_all": None, "allow_domains": ["a.example"]}},
        {"network_policy": {"allow_domains": ["a.example"]}},
    ),
]


@pytest.mark.parametrize(
    ("route", "field", "with_none", "without"), _NESTED, ids=[case[1] for case in _NESTED]
)
async def test_a_nested_none_is_omitted(
    route: str, field: str, with_none: dict[str, object], without: dict[str, object]
) -> None:
    del field
    assert await _body(route, with_none) == await _body(route, without)


@pytest.mark.parametrize(
    "options",
    [
        {"ports": [PortMapping(port=None)]},  # type: ignore[arg-type]
        {"ports": [PortMapping(port=3000, proto=None)]},  # type: ignore[arg-type]
        {"volumes": [VolumeMount(volume_id=None, mount_path="/m")]},  # type: ignore[arg-type]
        {"network_policy": {"allow_cidrs": None}},
    ],
    ids=["ports[].port", "ports[].proto", "volumes[].volume_id", "network_policy.allow_cidrs"],
)
async def test_a_nested_none_python_refuses_is_refused(options: dict[str, object]) -> None:
    with FakeApi([]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError):
                await _ROUTES["create"](root.v2, options)
    assert api.requests == []
