# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Create-from-build uploads local sources before creating the sandbox."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import anyio
import pytest
from network_policy_test_server import FakeApi, JsonValue, PlannedResponse
from test_build_context_upload import UPLOAD_ID, storage_peer
from test_image_context_upload import uploaded_bytes

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import BadRequestError, ImageBuilder
from daytona.v2._image_context import write_builder_context


@pytest.fixture(params=[False, True], ids=["async", "generated-sync"])
def sync(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


async def create(builder: ImageBuilder, sync: bool, *, wait_seconds: int = 0) -> str:
    if sync:

        def run() -> str:
            with Daytona(DaytonaConfig(api_key="offline")) as root:
                return root.v2.create_from_build(builder, region="region-c", wait_seconds=wait_seconds).id

        return await anyio.to_thread.run_sync(run)
    async with AsyncDaytona(DaytonaConfig(api_key="offline")) as root:
        sandbox = await root.v2.create_from_build(builder, region="region-c", wait_seconds=wait_seconds)
        return sandbox.id


@pytest.mark.anyio
async def test_create_from_build_uploads_context_and_creates_from_the_built_image(
    sync: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a local-source builder, real storage, and a planned control-plane API.
    source = tmp_path / "app.py"
    source.write_text("print('local')\n")
    builder = ImageBuilder.from_registry("python:3.13-slim").add_local_file(source, "/app.py")
    expected_tar = tmp_path / "expected.tar"
    write_builder_context(builder, expected_tar)
    expected = expected_tar.read_bytes()
    with storage_peer() as storage:
        upload: dict[str, JsonValue] = {
            "id": UPLOAD_ID,
            "state": "uploading",
            "region_id": "region-c",
            "dockerfile": "Dockerfile",
            "bytes": len(expected),
            "sha256": hashlib.sha256(expected).hexdigest(),
            "parts": [{"part_number": 1, "bytes": len(expected), "url": storage.url + "/bucket/part/1"}],
        }
        with FakeApi(
            [
                PlannedResponse(201, upload),
                PlannedResponse(200, {**upload, "state": "ready", "parts": []}),
                PlannedResponse(202, {"id": "img-local", "status": "pending"}),
                PlannedResponse(
                    200,
                    {
                        "id": "img-local",
                        "status": "ready",
                        "name": None,
                        "anonymous": True,
                        "class": "linux",
                        "region_id": "region-c",
                        "region_ids": ["region-c"],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "retention": "idle",
                        "origin": "dockerfile",
                        "format_version": 2,
                        "format_outdated": False,
                        "platform": False,
                        "default": False,
                        "deprecated": False,
                    },
                ),
                PlannedResponse(201, {"id": "sbx-local", "status": "running", "wait_cap_seconds": 0}),
            ]
        ) as api:
            monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
            # When create-from-build runs through the public root client.
            sandbox_id = await create(builder, sync)
    # Then upload and verification precede image submission and sandbox creation.
    assert sandbox_id == "sbx-local"
    assert [(request.method, request.path) for request in api.requests] == [
        ("POST", "/build-contexts"),
        ("POST", f"/build-contexts/{UPLOAD_ID}/complete"),
        ("POST", "/images"),
        ("GET", "/images/img-local"),
        ("POST", "/sandboxes"),
    ]
    assert json.loads(api.requests[0].body) == {
        "dockerfile": "Dockerfile",
        "bytes": len(expected),
        "sha256": hashlib.sha256(expected).hexdigest(),
        "region_id": "region-c",
    }
    assert uploaded_bytes(storage) == expected
    assert json.loads(api.requests[2].body) == {"context_upload_id": UPLOAD_ID, "region_id": "region-c"}
    sandbox_body = json.loads(api.requests[-1].body)
    assert sandbox_body["image"] == "img-local"
    assert sandbox_body["region_id"] == "region-c"


@pytest.mark.anyio
async def test_invalid_create_option_is_refused_before_local_files_are_read(
    sync: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a local-source builder whose file disappeared and an invalid wait.
    source = tmp_path / "app.py"
    source.write_text("local")
    builder = ImageBuilder.from_registry("alpine").add_local_file(source, "/app.py")
    source.unlink()
    with FakeApi([]) as api:
        monkeypatch.setenv("DAYTONA_V2_API_URL", api.url)
        # When create-from-build validates the create options.
        with pytest.raises(BadRequestError, match="wait_seconds"):
            await create(builder, sync, wait_seconds=-1)
    # Then the create error wins without any build or upload request.
    assert api.requests == []
