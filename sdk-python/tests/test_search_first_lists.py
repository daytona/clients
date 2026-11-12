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

from daytona import AsyncDaytona, AsyncDaytonaV2Client, Daytona, DaytonaConfig
from daytona.v2 import BadRequestError, DaytonaError, NotFoundError, SearchUnavailableError
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


@pytest.mark.parametrize(("status", "code"), [(501, "SEARCH_NOT_CONFIGURED"), (404, "NOT_FOUND")])
async def test_a_client_remembers_a_deployment_without_search(case: _Case, status: int, code: str) -> None:
    # Once a kind's search route answers "not served", the client's later calls
    # for that kind (the pages of an iter() walk included) go straight to the
    # list route.
    responses = [
        PlannedResponse(status, _error(code)),
        PlannedResponse(200, {case.field: [case.item], "next_cursor": None}),
        PlannedResponse(200, {case.field: [case.item], "next_cursor": None}),
    ]

    async def twice(v2: AsyncDaytonaV2Client) -> object:
        await case.call(v2)
        return await case.call(v2)

    page, paths = await _run(responses, twice)
    assert paths == [case.search_path + case.query, case.list_path + case.query, case.list_path + case.query]
    assert [item.id for item in page.items] == [case.item["id"]]


async def test_an_iter_walk_probes_search_once_without_search() -> None:
    responses = [
        PlannedResponse(501, _error("SEARCH_NOT_CONFIGURED")),
        PlannedResponse(200, {"images": [_IMAGE], "next_cursor": "p2"}),
        PlannedResponse(200, {"images": [{**_IMAGE, "id": "img_2"}], "next_cursor": None}),
    ]

    async def walk(v2: AsyncDaytonaV2Client) -> list[str]:
        return [image.id async for image in v2.images.iter(limit=1)]

    ids, paths = await _run(responses, walk)
    assert ids == ["img_1", "img_2"]
    assert paths == [
        "/images/search?limit=1&anonymous=false",
        "/images?limit=1&anonymous=false",
        "/images?cursor=p2&limit=1&anonymous=false",
    ]


async def test_a_503_is_not_remembered(case: _Case) -> None:
    body = {case.field: [case.item], "next_cursor": None, "total": 1, "total_relation": "eq"}
    responses = [PlannedResponse(503, _error("SEARCH_UNAVAILABLE")), PlannedResponse(200, body)]

    async def twice(v2: AsyncDaytonaV2Client) -> object:
        with pytest.raises(SearchUnavailableError):
            await case.call(v2)
        return await case.call(v2)

    page, paths = await _run(responses, twice)
    assert paths == [case.search_path + case.query, case.search_path + case.query]
    assert [item.id for item in page.items] == [case.item["id"]]


def test_the_sync_client_remembers_a_deployment_without_search() -> None:
    responses = [
        PlannedResponse(501, _error("SEARCH_NOT_CONFIGURED")),
        PlannedResponse(200, {"snapshots": [_SNAPSHOT], "next_cursor": None}),
        PlannedResponse(200, {"snapshots": [_SNAPSHOT], "next_cursor": None}),
    ]
    with FakeApi(responses) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        with Daytona(DaytonaConfig(api_key="key")) as root:
            root.v2.snapshots.list()
            page = root.v2.snapshots.list()
        paths = [request.path for request in api.requests]
    assert paths == ["/snapshots/search", "/snapshots", "/snapshots"]
    assert [item.id for item in page.items] == ["snap_1"]


@pytest.mark.parametrize(("service", "kind"), [("images", "image"), ("snapshots", "snapshot")])
async def test_search_cannot_be_looked_up_by_name(service: str, kind: str) -> None:
    error, paths = await _run([], lambda v2: getattr(v2, service).get("search"))
    assert paths == []
    assert isinstance(error, BadRequestError)
    assert (error.status_code, error.code, error.source) == (400, "BAD_REQUEST", "SDK")
    assert str(error) == (
        f"'search' cannot be looked up by name; it is reserved for the search route — use the {kind} ID"
    )


@pytest.mark.parametrize("service", ["images", "snapshots"])
def test_the_sync_client_refuses_search_as_a_name(service: str) -> None:
    with FakeApi([]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        with (
            Daytona(DaytonaConfig(api_key="key")) as root,
            pytest.raises(BadRequestError, match="reserved for the search route"),
        ):
            getattr(root.v2, service).get("search")
        assert api.requests == []


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
