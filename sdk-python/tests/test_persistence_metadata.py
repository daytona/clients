# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Immutable persistence survives sparse metadata reads."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from test_client_lifecycle_contract import _detail

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError

_BASE = "https://api.invalid"


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    ("sandbox_class", "action"),
    [("gpu", "stop"), ("gpu", "pause"), ("macos", "pause"), ("future-class", "pause")],
)
async def test_class_forbids_policy_even_when_persistence_is_unknown(
    monkeypatch: pytest.MonkeyPatch, sandbox_class: str, action: str
) -> None:
    # Given a known class whose detail still omits persistence.
    monkeypatch.setenv("DAYTONA_V2_API_URL", _BASE)
    read = respx.get(f"{_BASE}/sandboxes/sbx").mock(
        return_value=httpx.Response(200, json=_detail("sbx", "running", **{"class": sandbox_class}))
    )
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        sandbox = await root.v2.get("sbx")
        # When requesting an action forbidden by class alone.
        with pytest.raises(BadRequestError):
            await sandbox.update_lifecycle_policy(on_timeout=action)
    # Then only the initial detail and persistence refresh were requested.
    assert read.call_count == 2
    assert all(call.request.method == "GET" for call in respx.calls)


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("sandbox_class", ["gpu", "future-class"])
async def test_delete_policy_is_sent_for_every_raw_class(
    monkeypatch: pytest.MonkeyPatch, sandbox_class: str
) -> None:
    # Given a class outside the SDK's Linux/macOS lifecycle-verb vocabulary.
    monkeypatch.setenv("DAYTONA_V2_API_URL", _BASE)
    read = respx.get(f"{_BASE}/sandboxes/sbx").mock(
        return_value=httpx.Response(
            200, json=_detail("sbx", "running", **{"class": sandbox_class})
        )
    )
    patch = respx.patch(f"{_BASE}/sandboxes/sbx/lifecycle-policy").mock(
        return_value=httpx.Response(200, json={"id": "sbx", "lifecycle_policy": {"on_timeout": "delete"}})
    )
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        sandbox = await root.v2.get("sbx")
        # When setting the universally supported delete policy.
        policy = await sandbox.update_lifecycle_policy(on_timeout="delete")
    # Then the raw class does not prevent the public method from reaching PATCH.
    assert policy.on_timeout == "delete"
    assert read.call_count == 1
    assert patch.call_count == 1
    assert json.loads(patch.calls[0].request.content) == {"on_timeout": "delete"}


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("reported", [None, False, True])
async def test_waiting_create_preserves_known_persistence(
    monkeypatch: pytest.MonkeyPatch, reported: bool | None
) -> None:
    # Given a persistent create followed by an optional persistence field.
    monkeypatch.setenv("DAYTONA_V2_API_URL", _BASE)
    respx.post(f"{_BASE}/sandboxes").mock(
        return_value=httpx.Response(201, json={"id": "sbx", "status": "pending"})
    )
    detail = _detail("sbx", "running")
    if reported is not None:
        detail["persistent"] = reported
    read = respx.get(f"{_BASE}/sandboxes/sbx").mock(return_value=httpx.Response(200, json=detail))
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        # When readiness reads replace the create handle's metadata.
        sandbox = await root.v2.create(persistent=True, wait_seconds=1)
    # Then omitted persistence cannot erase a known value; an explicit bool wins.
    assert sandbox.state == "started"
    assert sandbox.persistent is (True if reported is None else reported)
    assert read.call_count >= 1


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize("explicit", ["neither", "source", "fork"])
async def test_waiting_fork_inherits_persistence_through_sparse_reads(
    monkeypatch: pytest.MonkeyPatch, explicit: str
) -> None:
    # Given known persistence before the source refresh and the fork detail read.
    monkeypatch.setenv("DAYTONA_V2_API_URL", _BASE)
    respx.post(f"{_BASE}/sandboxes").mock(
        return_value=httpx.Response(201, json={"id": "src", "status": "running"})
    )
    source_detail = _detail("src", "running")
    fork_detail = _detail("fork", "running")
    if explicit == "source":
        source_detail["persistent"] = False
    if explicit == "fork":
        fork_detail["persistent"] = False
    source_read = respx.get(f"{_BASE}/sandboxes/src").mock(
        return_value=httpx.Response(200, json=source_detail)
    )
    respx.post(f"{_BASE}/sandboxes/src/fork").mock(
        return_value=httpx.Response(202, json={"id": "fork", "status": "pending", "forked_from": "src"})
    )
    fork_read = respx.get(f"{_BASE}/sandboxes/fork").mock(
        return_value=httpx.Response(200, json=fork_detail)
    )
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        source = await root.v2.create(persistent=True, wait_seconds=0)
        # When a waiting fork refreshes both handles without persistence fields.
        fork = await source.fork(wait_seconds=1)
    # Then source knowledge survives omissions while explicit server values win.
    assert fork.state == "started"
    assert fork.persistent is (explicit == "neither")
    assert source.persistent is (explicit != "source")
    assert source_read.call_count == 1
    assert fork_read.call_count == 1
