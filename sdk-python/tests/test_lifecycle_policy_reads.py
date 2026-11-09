# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""``update_lifecycle_policy()`` reads the sandbox first only when its class is
unknown, or for a macOS sandbox whose persistence is unknown, as TypeScript
does: a known Linux sandbox sends the PATCH alone. The TypeScript twin is
``tests/lifecycle-policy-reads.test.ts``."""

from __future__ import annotations

import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2.types import JsonObject, JsonValue

pytestmark = pytest.mark.anyio

_ECHO: JsonObject = {"id": "sbx", "lifecycle_policy": {"timeout_seconds": 120}}


def _detail(sandbox_class: str, **extra: JsonValue) -> JsonObject:
    return {
        "id": "sbx",
        "class": sandbox_class,
        "name": "sbx",
        "status": "running",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        **extra,
    }


@pytest.mark.parametrize(
    ("known", "answers", "sent"),
    [
        ("linux", [], ["PATCH"]),
        (None, [_detail("linux")], ["GET", "PATCH"]),
        ("macos", [_detail("macos", persistent=True)], ["GET", "PATCH"]),
    ],
    ids=["known linux", "unknown class", "macos, persistence unknown"],
)
async def test_the_update_reads_the_sandbox_only_when_it_must(
    monkeypatch: pytest.MonkeyPatch, known: str | None, answers: list[JsonObject], sent: list[str]
) -> None:
    with FakeApi(
        [*(PlannedResponse(200, answer) for answer in answers), PlannedResponse(200, _ECHO)]
    ) as fake:
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            if known is not None:
                sandbox._update(sandbox_class=known, raw_sandbox_class=known)  # type: ignore[arg-type]
            await sandbox.update_lifecycle_policy(timeout_seconds=120)
    assert [request.method for request in fake.requests] == sent
