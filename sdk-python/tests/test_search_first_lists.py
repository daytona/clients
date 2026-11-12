# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lists read the search route first and the plain list route only where the
deployment does not serve search (501 ``SEARCH_NOT_CONFIGURED``, or 404 from an
API without the route); a name reads the item route. The TypeScript twin is
tests/search-first-lists.test.ts."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import DaytonaError, NotFoundError, SearchUnavailableError
from daytona.v2.services import _search_not_served

pytestmark = pytest.mark.anyio

_SANDBOX = {
    "id": "sbx",
    "class": "linux",
    "name": "sbx",
    "status": "running",
    "toolbox_proxy_url": "https://proxy.example/sandbox-api",
    "cpu": 1,
    "cores_max": 1,
    "memory_mib": 1024,
    "disk_mib": 1024,
    "region_id": "r1",
    "port_map": [],
}
_IMAGE = {
    "id": "img_1",
    "name": "base",
    "anonymous": False,
    "class": "linux",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "origin": "registry",
    "region_id": "r1",
    "retention": "permanent",
    "status": "ready",
    "format_version": 2,
    "format_outdated": False,
    "region_ids": ["r1"],
    "platform": False,
    "default": False,
    "deprecated": False,
}
_SNAPSHOT = {
    "id": "snap_1",
    "name": "after-setup",
    "class": "linux",
    "kind": "full",
    "status": "ready",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "region_id": "r1",
    "region_ids": ["r1"],
}

Call = Callable[[Any], Awaitable[Any]]


@dataclass(frozen=True)
class _Case:
    search_path: str
    list_path: str
    field: str
    item: dict[str, object]
    call: Call
    query: str


_CASES = {
    "sandboxes": _Case(
        "/sandboxes/search",
        "/sandboxes",
        "sandboxes",
        _SANDBOX,
        lambda v2: v2.list(cursor="c1", limit=5),
        "?cursor=c1&limit=5",
    ),
    "images": _Case(
        "/images/search",
        "/images",
        "images",
        _IMAGE,
        lambda v2: v2.images.list(name="base", limit=5),
        "?limit=5&anonymous=false&name=base",
    ),
    "snapshots": _Case(
        "/snapshots/search",
        "/snapshots",
        "snapshots",
        _SNAPSHOT,
        lambda v2: v2.snapshots.list(sandbox_id="sbx", limit=5),
        "?limit=5&sandbox_id=sbx",
    ),
}


def _error(code: str) -> dict[str, object]:
    return {"code": code, "message": code.lower(), "source": "DAYTONA_API"}


async def _run(responses: list[PlannedResponse], call: Call) -> tuple[Any, list[str]]:
    with FakeApi(responses) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            try:
                result: Any = await call(root.v2)
            except DaytonaError as error:
                result = error
        return result, [request.path for request in api.requests]


@pytest.fixture(params=sorted(_CASES))
def case(request: pytest.FixtureRequest) -> _Case:
    return _CASES[request.param]


async def test_a_list_reads_the_search_route_where_search_is_served(case: _Case) -> None:
    body = {case.field: [case.item], "next_cursor": "n1", "total": 1, "total_relation": "eq"}
    page, paths = await _run([PlannedResponse(200, body)], case.call)
    assert paths == [case.search_path + case.query]
    assert [item.id for item in page.items] == [case.item["id"]]
    assert page.next_cursor == "n1"


@pytest.mark.parametrize(("status", "code"), [(501, "SEARCH_NOT_CONFIGURED"), (404, "NOT_FOUND")])
async def test_a_list_falls_back_to_the_list_route_where_search_is_not_served(
    case: _Case, status: int, code: str
) -> None:
    responses = [
        PlannedResponse(status, _error(code)),
        PlannedResponse(200, {case.field: [case.item], "next_cursor": None}),
    ]
    page, paths = await _run(responses, case.call)
    assert paths == [case.search_path + case.query, case.list_path + case.query]
    assert [item.id for item in page.items] == [case.item["id"]]
    assert page.next_cursor is None


async def test_a_list_raises_503_search_unavailable_without_falling_back(case: _Case) -> None:
    error, paths = await _run([PlannedResponse(503, _error("SEARCH_UNAVAILABLE"))], case.call)
    assert paths == [case.search_path + case.query]
    assert isinstance(error, SearchUnavailableError)
    assert (error.status_code, error.code) == (503, "SEARCH_UNAVAILABLE")


async def test_a_list_raises_a_501_with_another_code_without_falling_back(case: _Case) -> None:
    error, paths = await _run([PlannedResponse(501, _error("NOT_IMPLEMENTED"))], case.call)
    assert paths == [case.search_path + case.query]
    assert isinstance(error, DaytonaError)
    assert (error.status_code, error.code) == (501, "NOT_IMPLEMENTED")


@pytest.mark.parametrize(
    ("service", "name", "body", "not_found"),
    [
        ("images", "base", _IMAGE, "IMAGE_NOT_FOUND"),
        ("snapshots", "after-setup", _SNAPSHOT, "SNAPSHOT_NOT_FOUND"),
    ],
)
async def test_a_name_reads_the_item_route_and_404_is_absent(
    service: str, name: str, body: dict[str, object], not_found: str
) -> None:
    found, paths = await _run([PlannedResponse(200, body)], lambda v2: getattr(v2, service).get(name))
    assert paths == [f"/{service}/{name}"]
    assert found.id == body["id"]
    absent, paths = await _run(
        [PlannedResponse(404, _error(not_found))], lambda v2: getattr(v2, service).get("missing")
    )
    assert paths == [f"/{service}/missing"]
    assert isinstance(absent, NotFoundError)


def test_search_not_served_is_501_search_not_configured_or_404_only() -> None:
    assert _search_not_served(DaytonaError("x", status_code=501, code="SEARCH_NOT_CONFIGURED"))
    assert _search_not_served(DaytonaError("x", status_code=404, code="NOT_FOUND"))
    assert not _search_not_served(DaytonaError("x", status_code=503, code="SEARCH_UNAVAILABLE"))
    assert not _search_not_served(DaytonaError("x", status_code=410, code="GONE"))
    assert not _search_not_served(DaytonaError("x", status_code=501, code="NOT_IMPLEMENTED"))
