# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``resources.cpu_max`` is a positive multiple of 0.5 cores (the contract's
``cores_max``): any other value is ``BadRequestError`` naming it, before any
request, on ``create()`` and ``create_from_build()`` (before the build). The
TypeScript twin is ``tests/cpu-max-step.test.ts``."""

from __future__ import annotations

import json

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError, ImageBuilder, Resources

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cpu_max", [1.1, 0.3])
@pytest.mark.parametrize("route", ["create", "create_from_build"])
async def test_a_cpu_max_off_the_half_core_step_is_refused(
    monkeypatch: pytest.MonkeyPatch, cpu_max: float, route: str
) -> None:
    with FakeApi([]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            resources = Resources(cpu_max=cpu_max)
            with pytest.raises(BadRequestError) as raised:
                if route == "create":
                    await root.v2.create("img_1", resources=resources, wait_seconds=0)
                else:
                    await root.v2.create_from_build(
                        ImageBuilder.from_registry("alpine"), resources=resources, wait_seconds=0
                    )
    assert str(raised.value) == "resources.cpu_max must be a positive multiple of 0.5 cores"
    assert fake.requests == []


@pytest.mark.parametrize("cpu_max", [0.5, 1, 1.5])
async def test_a_cpu_max_on_the_half_core_step_is_sent(
    monkeypatch: pytest.MonkeyPatch, cpu_max: float
) -> None:
    with FakeApi([PlannedResponse(200, {"id": "sbx", "status": "running"})]) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.create("img_1", resources=Resources(cpu_max=cpu_max), wait_seconds=0)
    assert json.loads(fake.requests[0].body)["cores_max"] == cpu_max
