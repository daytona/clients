# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Desktop create bodies and immutable create-time sandbox metadata."""

from __future__ import annotations

import dataclasses
import json
from typing import Literal

import pytest
import respx
from _handles import lazy_sandbox
from typing_extensions import assert_never

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import (
    BadRequestError,
    DesktopConfig,
    DesktopResolution,
    DesktopSettings,
    ImageBuilder,
    SandboxEventsConfig,
)
from daytona.v2._gen.api import Sandbox, SandboxList, SandboxListItem
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import JsonObject, JsonValue


def _desktop(environment: str = "gnome") -> JsonObject:
    return {"environment": environment, "resolution": {"width": 1920, "height": 1080}}


def _detail(desktop: JsonValue) -> JsonObject:
    return {
        "id": "sbx",
        "class": "linux",
        "name": "example",
        "status": "running",
        "cpu": 1,
        "cores_max": 2,
        "memory_mib": 8192,
        "disk_mib": 2048,
        "region_id": "region-a",
        "port_map": [],
        "toolbox_proxy_url": "https://proxy.invalid",
        **({"desktop": desktop} if desktop is not None else {}),
    }


@pytest.mark.anyio
@pytest.mark.parametrize("image", [None, "base-image"])
@pytest.mark.parametrize(
    "desktop",
    [
        None,
        {},
        {"environment": "gnome"},
        {"resolution": {"width": 1024, "height": 768}},
        {"environment": "xfce", "resolution": {"width": 1280, "height": 720}},
    ],
)
@respx.mock
async def test_create_preserves_desktop_body(
    monkeypatch: pytest.MonkeyPatch, image: str | None, desktop: DesktopConfig | None
) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    route = respx.post("https://api.invalid/sandboxes").respond(200, json={"id": "sbx"})
    # When
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        await root.v2.create(image, desktop=desktop, wait_seconds=0)
    # Then
    body = json.loads(route.calls[0].request.content)
    assert ("desktop" in body) is (desktop is not None)
    if desktop is not None:
        assert body["desktop"] == desktop
    assert ("image" in body) is (image is not None)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["get", "list", "search", "create", "wait"])
@pytest.mark.parametrize("environment", [None, "xfce", "gnome", "future-desktop"])
@respx.mock
async def test_metadata_mapping_across_api_seams(
    monkeypatch: pytest.MonkeyPatch,
    operation: Literal["get", "list", "search", "create", "wait"],
    environment: str | None,
) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    desktop = _desktop(environment) if environment is not None else None
    detail = _detail(desktop)
    respx.get("https://api.invalid/sandboxes/sbx").respond(200, json=detail)
    respx.get("https://api.invalid/sandboxes/search").respond(
        200, json={"sandboxes": [detail], "next_cursor": None, "total": 1, "total_relation": "eq"}
    )
    respx.post("https://api.invalid/sandboxes").respond(
        200,
        json={
            "id": "sbx",
            "status": "pending" if operation == "wait" else "running",
            **({"desktop": desktop} if desktop and operation != "wait" else {}),
        },
    )
    # When
    async with AsyncDaytona(DaytonaConfig(api_key="key", events=SandboxEventsConfig(enabled=False))) as root:
        match operation:
            case "get":
                sandbox = await root.v2.get("sbx")
            case "list":
                sandbox = (await root.v2.list()).items[0]
            case "search":
                sandbox = (await root.v2.search()).items[0]
            case "create":
                sandbox = await root.v2.create(wait_seconds=0)
            case "wait":
                sandbox = await root.v2.create(wait_seconds=0)
                await sandbox.wait(until="started", timeout=1)
            case unreachable:
                assert_never(unreachable)
    # Then
    if environment is None:
        assert sandbox.desktop is None
        assert "desktop" not in sandbox.to_json()
    else:
        expected = environment if environment in ("xfce", "gnome") else "unknown"
        assert sandbox.desktop == DesktopSettings(
            environment=expected,
            raw_environment=environment,
            resolution=DesktopResolution(width=1920, height=1080),
        )
        assert sandbox.to_json()["desktop"] == {
            "environment": expected,
            "rawEnvironment": environment,
            "resolution": {"width": 1920, "height": 1080},
        }
        with pytest.raises(dataclasses.FrozenInstanceError):
            sandbox.desktop.resolution.width = 1


@pytest.mark.parametrize("dto", [Sandbox, SandboxListItem])
def test_generated_dto_conversion_preserves_desktop(dto: type[Sandbox | SandboxListItem]) -> None:
    # Given
    wire = dto.from_dict({**_detail(_desktop("future-desktop")), "cursor": "next"})
    assert wire is not None
    # When
    sandbox = AsyncSandbox._from_api(wire, None, None)
    # Then
    assert sandbox.desktop is not None
    assert sandbox.desktop.environment == "unknown"
    assert sandbox.desktop.raw_environment == "future-desktop"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "desktop",
    [
        False,
        {},
        {"environment": "gnome"},
        {"environment": 1, "resolution": {"width": 1, "height": 1}},
        {"environment": "xfce", "resolution": {"width": "1280", "height": 720}},
        {"environment": "xfce", "resolution": {"width": -1, "height": 720}},
    ],
)
@respx.mock
async def test_detail_rejects_malformed_desktop(monkeypatch: pytest.MonkeyPatch, desktop: JsonValue) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    respx.get("https://api.invalid/sandboxes/sbx").respond(200, json=_detail(desktop))
    # When / Then
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="desktop|environment|width") as caught:
            await root.v2.get("sbx")
    assert caught.value.source == "SDK"


@pytest.mark.anyio
@respx.mock
async def test_create_rejects_malformed_desktop_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    respx.post("https://api.invalid/sandboxes").respond(200, json={"id": "sbx", "desktop": {}})
    # When
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="desktop") as caught:
            await root.v2.create(wait_seconds=0)
    # Then
    assert caught.value.source == "SDK"


@pytest.mark.parametrize("sync", [False, True])
@respx.mock
def test_snapshot_and_fork_refuse_desktop_keyword(sync: bool) -> None:
    # Given
    client = Daytona if sync else AsyncDaytona
    root = client(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(root.v2, "sbx")
    # When / Then: Python's explicit signatures reject unsupported keywords.
    with pytest.raises(TypeError, match="desktop"):
        root.v2.create_from_snapshot("snap", desktop={})
    with pytest.raises(TypeError, match="desktop"):
        sandbox.fork(desktop={})
    assert not respx.calls
    if sync:
        root.close()


@respx.mock
def test_sync_create_exposes_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    route = respx.post("https://api.invalid/sandboxes").respond(
        200, json={"id": "sbx", "desktop": _desktop()}
    )
    # When
    with Daytona(DaytonaConfig(api_key="key")) as root:
        sandbox = root.v2.create(desktop={}, wait_seconds=0)
    # Then
    assert json.loads(route.calls[0].request.content)["desktop"] == {}
    assert sandbox.desktop is not None
    assert sandbox.desktop.raw_environment == "gnome"


def test_batch_wait_snapshot_without_desktop_keeps_it_absent() -> None:
    # Given: GET /sandboxes/batch-wait returns SandboxList, not a new SDK route.
    snapshot = SandboxList.from_dict({"sandboxes": [_detail(None)]})
    assert snapshot is not None
    # When: use the same generated Sandbox -> handle conversion seam.
    sandbox = AsyncSandbox._from_api(snapshot.sandboxes[0], None, None)
    # Then
    assert sandbox.desktop is None
    assert "desktop" not in sandbox.to_json()


@pytest.mark.anyio
@pytest.mark.parametrize("desktop", [{}, {"environment": "gnome"}])
@respx.mock
async def test_build_forwards_desktop_only_to_final_image_create(
    monkeypatch: pytest.MonkeyPatch, desktop: DesktopConfig
) -> None:
    # Given
    monkeypatch.setenv("DAYTONA_V2_API_URL", "https://api.invalid")
    build = respx.post("https://api.invalid/images").respond(
        200, json={"id": "img_built", "status": "pending"}
    )
    respx.get("https://api.invalid/images/img_built").respond(
        200,
        json={
            "id": "img_built",
            "name": "built",
            "anonymous": True,
            "class": "linux",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "status": "ready",
            "origin": "registry",
            "source": {"type": "registry", "ref": "python:3.12"},
            "region_id": "region-a",
            "region_ids": ["region-a"],
            "retention": "permanent",
            "format_outdated": False,
            "format_version": 2,
            "platform": False,
            "default": False,
            "deprecated": False,
        },
    )
    create = respx.post("https://api.invalid/sandboxes").respond(200, json={"id": "sbx"})
    # When
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        await root.v2.create_from_build(
            ImageBuilder.from_registry("python:3.12"), desktop=desktop, wait_seconds=0
        )
    # Then
    assert "desktop" not in json.loads(build.calls[0].request.content)
    assert json.loads(create.calls[0].request.content) == {
        "image": "img_built",
        "desktop": desktop,
        "wait_seconds": 0,
    }
