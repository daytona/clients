# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Response models that stay readable when a later API adds to them, and the
absent-value convention: an image source kind this release does not know is
an ``UnknownImageSource``, not a malformed response; labels a create response
does not carry are unknown (``None``), not empty; an empty ``next_cursor`` ends
an iteration. The TypeScript twins are in tests/artifact-models.test.ts,
tests/malformed-responses.test.ts and tests/list-pages.test.ts."""

from __future__ import annotations

import os

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError, UnknownImageSource
from daytona.v2._api_core import expect_image, expect_images_page

pytestmark = pytest.mark.anyio


def _image(image_id: str, **changes: object) -> dict[str, object]:
    return {
        "id": image_id,
        "anonymous": False,
        "class": "linux",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "origin": "registry",
        "region_id": "region-a",
        "retention": "permanent",
        "status": "ready",
        "format_version": 2,
        "format_outdated": False,
        "region_ids": ["region-a"],
        "platform": False,
        "default": False,
        "deprecated": False,
        "cursor": image_id,
        **changes,
    }


def test_an_unknown_source_kind_keeps_its_wire_type() -> None:
    image = expect_image(_image("img", source={"type": "oci_layout", "uri": "oci://example"}))
    assert image.source == UnknownImageSource(raw_type="oci_layout")
    assert image.source.type == "unknown"


def test_a_list_page_maps_each_unknown_source_and_checks_the_known_ones() -> None:
    page = expect_images_page(
        {
            "images": [
                _image("img-1", source={"type": "registry", "ref": "alpine"}),
                _image("img-2", source={"type": "future", "extra": 1}),
            ],
            "next_cursor": None,
        }
    )
    assert [item.source for item in page.items][1] == UnknownImageSource(raw_type="future")
    assert page.items[0].source is not None and page.items[0].source.type == "registry"


@pytest.mark.parametrize("source", [{"type": "registry"}, {"type": 7}, {"type": ""}, {"ref": "alpine"}])
def test_a_malformed_source_is_still_the_malformed_response_error(source: dict[str, object]) -> None:
    with pytest.raises(BadRequestError) as raised:
        expect_image(_image("img", source=source))
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


async def test_a_create_handle_has_unknown_labels_until_a_detail_read() -> None:
    detail = {
        "id": "sbx",
        "name": "named",
        "status": "running",
        "class": "linux",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        "labels": {"name": "named", "team": "a"},
    }
    with FakeApi([PlannedResponse(201, {"id": "sbx"}), PlannedResponse(200, detail)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = await root.v2.create("img_1", labels={"team": "a"}, name="named", wait_seconds=0)
            # The create response carried neither labels nor a status.
            assert sandbox.labels is None
            assert (sandbox.state, sandbox.raw_state) == ("unknown", None)
            assert "labels" not in sandbox.to_json()
            await sandbox.refresh_data()
            assert sandbox.labels == {"name": "named", "team": "a"}
            assert (sandbox.state, sandbox.raw_state) == ("started", "running")


async def test_an_empty_next_cursor_ends_an_iteration() -> None:
    with FakeApi([PlannedResponse(200, {"images": [_image("img-1")], "next_cursor": ""})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            seen = [image.id async for image in root.v2.images.iter()]
        assert seen == ["img-1"]
        assert len(api.requests) == 1
