# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""One SandboxHandleJSON for both SDKs, byte for byte.

The fixture is shared with the TypeScript suite
(``sdk-typescript/tests/handle-json-fixture.test.ts``): the same sandbox detail
must serialize to exactly the same JSON, timestamp strings included.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from daytona import AsyncDaytona, Daytona, DaytonaConfig

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sandbox-handle-json.json").read_text())
_API = "https://api.handle-json.test"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> respx.MockRouter:
    monkeypatch.setenv("DAYTONA_V2_API_URL", _API)
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "0")
    with respx.mock(assert_all_called=False) as router:
        detail = _FIXTURE["detail"]
        router.get(f"{_API}/sandboxes/sbx-ts").mock(return_value=httpx.Response(200, json=detail))
        router.post(f"{_API}/sandboxes/sbx-ts/fork").mock(
            return_value=httpx.Response(
                202,
                json={
                    "id": "sbx-ts",
                    "status": "running",
                    "forked_from": "src",
                    "toolbox_proxy_url": detail["toolbox_proxy_url"],
                },
            )
        )
        yield router


@pytest.mark.anyio
async def test_async_get_and_fork_serialize_the_shared_fixture_byte_for_byte(api: respx.MockRouter) -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        sandbox = await root.v2.get("sbx-ts")
        # A positive budget reads the fork's detail (wait_seconds=0 does not).
        forked = await sandbox.fork(wait_seconds=5)
    expected = _canonical(_FIXTURE["handleJson"])
    assert _canonical(sandbox.to_json()) == expected
    assert _canonical(forked.to_json()) == expected
    # Lifecycle actions are plain strings, never the generated enum.
    for handle in (sandbox, forked):
        policy = handle.lifecycle_policy
        assert policy is not None
        assert type(policy.on_timeout) is str and type(policy.on_idle_timeout) is str
        assert type(handle.to_json()["lifecyclePolicy"]["onTimeout"]) is str


def test_sync_get_serializes_the_shared_fixture_byte_for_byte(api: respx.MockRouter) -> None:
    with Daytona(DaytonaConfig(api_key="key")) as root:
        sandbox = root.v2.get("sbx-ts")
        policy = sandbox.lifecycle_policy
        assert policy is not None and type(policy.on_timeout) is str and type(policy.on_idle_timeout) is str
        assert _canonical(sandbox.to_json()) == _canonical(_FIXTURE["handleJson"])


_CREATE_PATHS = {
    "create": "/sandboxes",
    "createFromSnapshot": "/sandboxes/from-snapshot",
    "createMacos": "/sandboxes/macos",
}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case", _FIXTURE["createResponses"], ids=lambda case: f"{case['call']}-{case['source']}"
)
async def test_a_handle_from_a_create_response_serializes_the_shared_fixture(
    monkeypatch: pytest.MonkeyPatch, case: dict[str, object]
) -> None:
    # Only what the response carries, an img_ / snap_ id the caller passed, and
    # the class the call fixes (macOS): never a name as an id, never a guessed class.
    monkeypatch.setenv("DAYTONA_V2_API_URL", _API)
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "0")
    call, source, region = str(case["call"]), str(case["source"]), case.get("region")
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{_API}{_CREATE_PATHS[call]}").mock(
            return_value=httpx.Response(200, json=case["response"])
        )
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            options: dict[str, object] = {"wait_seconds": 0}
            if region is not None:
                options["region"] = region
            if call == "create":
                sandbox = await root.v2.create(source, **options)  # type: ignore[arg-type]
            elif call == "createFromSnapshot":
                sandbox = await root.v2.create_from_snapshot(source, **options)  # type: ignore[arg-type]
            else:
                sandbox = await root.v2.create_macos(source, **options)  # type: ignore[arg-type]
    assert _canonical(sandbox.to_json()) == _canonical(case["handleJson"])
