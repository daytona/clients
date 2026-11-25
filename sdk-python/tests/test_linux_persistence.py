# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Persistence crosses create boundaries without leaking into snapshot overrides."""

from __future__ import annotations

import inspect
import json

import anyio
import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse
from test_client_lifecycle_contract import _detail

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import BadRequestError, ImageBuilder
from daytona.v2._core import assert_lifecycle_class_support
from daytona.v2._sync import Daytona as SyncClient
from daytona.v2.daytona import AsyncDaytona as AsyncClient
from daytona.v2.types import JsonObject

_IMAGE: JsonObject = {
    "id": "img-built",
    "status": "ready",
    "anonymous": True,
    "class": "linux",
    "region_id": "region-a",
    "region_ids": ["region-a"],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "retention": "idle",
    "origin": "dockerfile",
    "format_version": 2,
    "format_outdated": False,
    "platform": False,
    "default": False,
    "deprecated": False,
}


@pytest.mark.anyio
@pytest.mark.parametrize("sync", [False, True], ids=["async", "generated-sync"])
@pytest.mark.parametrize("build", [False, True], ids=["image", "build"])
@pytest.mark.parametrize("persistent", [None, False, True])
async def test_linux_create_preserves_persistence(
    monkeypatch: pytest.MonkeyPatch, sync: bool, build: bool, persistent: bool | None
) -> None:
    # Given a create that returns before a detail read can populate persistence.
    responses = [
        *(
            [PlannedResponse(202, {"id": "img-built", "status": "pending"}), PlannedResponse(200, _IMAGE)]
            if build
            else []
        ),
        PlannedResponse(201, {"id": "sbx", "status": "running"}),
    ]
    builder = ImageBuilder.from_registry("alpine")
    with FakeApi(responses) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        # When creating through either public client surface.
        if sync:

            def run() -> bool | None:
                with Daytona(DaytonaConfig(api_key="key")) as root:
                    sandbox = (
                        root.v2.create_from_build(builder, persistent=persistent, wait_seconds=0)
                        if build
                        else root.v2.create(persistent=persistent, wait_seconds=0)
                    )
                    return sandbox.persistent

            observed = await anyio.to_thread.run_sync(run)
        else:
            async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
                sandbox = (
                    await root.v2.create_from_build(builder, persistent=persistent, wait_seconds=0)
                    if build
                    else await root.v2.create(persistent=persistent, wait_seconds=0)
                )
                observed = sandbox.persistent
    # Then explicit values reach the wire and remain known on the handle.
    body = json.loads(api.requests[-1].body)
    assert observed is persistent
    assert ("persistent" in body) is (persistent is not None)
    if persistent is not None:
        assert body["persistent"] is persistent


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["stop", "pause"])
async def test_snapshot_policy_uses_inherited_persistence(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    # Given a snapshot with configuration known only to the server.
    with FakeApi([PlannedResponse(201, {"id": "sbx", "status": "running"})]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            # When creating with a lifecycle policy that may need persistence.
            sandbox = await root.v2.create_from_snapshot(
                "snap", wait_seconds=0, lifecycle_policy={"on_timeout": action}
            )
    # Then no source lookup or persistence override is invented.
    assert [request.path for request in api.requests] == ["/sandboxes/from-snapshot"]
    body = json.loads(api.requests[0].body)
    assert body["lifecycle_policy"] == {"on_timeout": action}
    assert "persistent" not in body
    assert sandbox.persistent is None


@pytest.mark.parametrize("client", [AsyncClient, SyncClient])
def test_snapshot_create_has_no_persistence_override(client: type[AsyncClient | SyncClient]) -> None:
    # Given either client surface, when its snapshot signature is inspected.
    parameters = inspect.signature(client.create_from_snapshot).parameters
    # Then only the source may supply persistence.
    assert "persistent" not in parameters


@pytest.mark.parametrize("sandbox_class", ["linux", "macos", "gpu"])
@pytest.mark.parametrize("persistent", [False, True, None])
@pytest.mark.parametrize("action", ["delete", "stop", "pause"])
def test_lifecycle_support_matrix(sandbox_class: str, persistent: bool | None, action: str) -> None:
    # Given a known class and known or inherited persistence.
    allowed = action == "delete" or (
        persistent is not False
        and (
            (action == "stop" and sandbox_class != "gpu") or (action == "pause" and sandbox_class == "linux")
        )
    )
    # When checking policy support, then only valid combinations survive.
    if allowed:
        assert_lifecycle_class_support(
            {"on_timeout": action}, sandbox_class=sandbox_class, persistent=persistent
        )
    else:
        with pytest.raises(BadRequestError):
            assert_lifecycle_class_support(
                {"on_timeout": action}, sandbox_class=sandbox_class, persistent=persistent
            )


@pytest.mark.anyio
@pytest.mark.parametrize("persistent", [True, False])
async def test_fork_fallback_preserves_source_persistence(
    monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    # Given a source whose persistence is already known.
    with FakeApi([PlannedResponse(202, {"id": "fork", "status": "pending", "forked_from": "src"})]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            source = lazy_sandbox(root.v2, "src")
            source._update(persistent=persistent)
            # When the fork returns without a detail read.
            fork = await source.fork(wait_seconds=0)
    # Then its inherited persistence is known too.
    assert fork.persistent is persistent


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["stop", "pause"])
async def test_persistent_linux_create_accepts_lifecycle_policy(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    # Given a persistent Linux sandbox with an automatic lifecycle action.
    with FakeApi([PlannedResponse(201, {"id": "sbx", "status": "running"})]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            # When creating with a supported policy.
            sandbox = await root.v2.create(
                persistent=True, wait_seconds=0, lifecycle_policy={"on_timeout": action}
            )
    # Then the selected action reaches the API.
    assert json.loads(api.requests[0].body)["lifecycle_policy"] == {"on_timeout": action}
    assert sandbox.persistent is True


@pytest.mark.anyio
@pytest.mark.parametrize("persistent", [False, True])
async def test_linux_policy_update_resolves_unknown_persistence(
    monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    # Given a known Linux class but unknown persistence.
    responses = [PlannedResponse(200, _detail("sbx", "running", persistent=persistent))]
    if persistent:
        responses.append(PlannedResponse(200, {"id": "sbx", "lifecycle_policy": {"on_timeout": "pause"}}))
    with FakeApi(responses) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            sandbox._update(sandbox_class="linux", raw_sandbox_class="linux")
            # When requesting a policy whose support depends on persistence.
            if persistent:
                result = await sandbox.update_lifecycle_policy(on_timeout="pause")
                assert result.on_timeout == "pause"
            else:
                with pytest.raises(BadRequestError):
                    await sandbox.update_lifecycle_policy(on_timeout="pause")
    # Then one detail read decides whether the update may be submitted.
    assert [request.method for request in api.requests] == (["GET", "PATCH"] if persistent else ["GET"])


@pytest.mark.anyio
@pytest.mark.parametrize("build", [False, True], ids=["image", "build"])
async def test_invalid_persistence_is_refused_before_submission(
    monkeypatch: pytest.MonkeyPatch, build: bool
) -> None:
    # Given an invalid bool, including on a create that would first bill a build.
    with FakeApi([]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            # When the create options are validated.
            with pytest.raises(BadRequestError, match="persistent"):
                if build:
                    await root.v2.create_from_build(ImageBuilder.from_registry("alpine"), persistent=1)
                else:
                    await root.v2.create(persistent=1)
    # Then neither a build nor a create was submitted.
    assert api.requests == []
