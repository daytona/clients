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
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, AsyncDaytonaV2Client, Daytona, DaytonaConfig
from daytona.v2 import BadRequestError, DaytonaError, NotFoundError, SearchUnavailableError
from daytona.v2.services import _search_not_served, _search_or_list, _SearchProbe

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
_SECRET = {
    "name": "TOKEN",
    "allowed_domains": ["example.com"],
    "description": "service token",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "cursor": "secret-cursor",
}
_VOLUME = {
    "id": "vol_1",
    "name": "data",
    "type": "disk",
    "status": "ready",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "cursor": "volume-cursor",
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
    identity: str


_CASES = {
    "sandboxes": _Case(
        "/sandboxes/search",
        "/sandboxes",
        "sandboxes",
        _SANDBOX,
        lambda v2: v2.list(cursor="c1", limit=5),
        "?cursor=c1&limit=5",
        "sbx",
    ),
    "images": _Case(
        "/images/search",
        "/images",
        "images",
        _IMAGE,
        lambda v2: v2.images.list(name="base", limit=5),
        "?limit=5&anonymous=false&name=base",
        "img_1",
    ),
    "snapshots": _Case(
        "/snapshots/search",
        "/snapshots",
        "snapshots",
        _SNAPSHOT,
        lambda v2: v2.snapshots.list(sandbox_id="sbx", limit=5),
        "?limit=5&sandbox_id=sbx",
        "snap_1",
    ),
    "secrets": _Case(
        "/secrets/search",
        "/secrets",
        "secrets",
        _SECRET,
        lambda v2: v2.secrets.list(cursor="c1", limit=5),
        "?cursor=c1&limit=5",
        "TOKEN",
    ),
    "volumes": _Case(
        "/volumes/search",
        "/volumes",
        "volumes",
        _VOLUME,
        lambda v2: v2.volumes.list(cursor="c1", limit=5),
        "?cursor=c1&limit=5",
        "vol_1",
    ),
}


def _identity(item: object) -> str:
    return item.name if not hasattr(item, "id") else item.id


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
    assert [_identity(item) for item in page.items] == [case.identity]
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
    assert [_identity(item) for item in page.items] == [case.identity]
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
    assert [_identity(item) for item in page.items] == [case.identity]


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
    assert [_identity(item) for item in page.items] == [case.identity]


async def test_secret_search_sends_every_filter_and_returns_totals() -> None:
    body = {"secrets": [_SECRET], "next_cursor": "next", "total": 1, "total_relation": "eq"}
    page, paths = await _run(
        [PlannedResponse(200, body)],
        lambda v2: v2.secrets.search(
            q="token",
            name="TOKEN",
            name_prefix="TOK",
            allowed_domain="example.com",
            created_start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            created_end_at="2026-02-01T00:00:00Z",
            updated_start_at="2026-01-02T00:00:00Z",
            updated_end_at="2026-02-02T00:00:00Z",
            sort="updated_at",
            order="desc",
            limit=5,
            cursor="cursor",
        ),
    )
    request = urlsplit(paths[0])
    assert request.path == "/secrets/search"
    assert parse_qs(request.query) == {
        "q": ["token"],
        "name": ["TOKEN"],
        "name_prefix": ["TOK"],
        "allowed_domain": ["example.com"],
        "created_start_at": ["2026-01-01T00:00:00Z"],
        "created_end_at": ["2026-02-01T00:00:00Z"],
        "updated_start_at": ["2026-01-02T00:00:00Z"],
        "updated_end_at": ["2026-02-02T00:00:00Z"],
        "sort": ["updated_at"],
        "order": ["desc"],
        "cursor": ["cursor"],
        "limit": ["5"],
    }
    assert (page.items[0].name, page.next_cursor, page.total, page.total_relation) == (
        "TOKEN",
        "next",
        1,
        "eq",
    )


async def test_volume_search_sends_every_filter_and_returns_totals() -> None:
    body = {"volumes": [_VOLUME], "next_cursor": None, "total": 1, "total_relation": "gte"}
    page, paths = await _run(
        [PlannedResponse(200, body)],
        lambda v2: v2.volumes.search(
            q="data",
            name="data",
            name_prefix="da",
            types=["disk", "bucket"],
            statuses=["ready", "error"],
            imported=False,
            created_start_at="2026-01-01T00:00:00Z",
            created_end_at="2026-02-01T00:00:00Z",
            updated_start_at="2026-01-02T00:00:00Z",
            updated_end_at="2026-02-02T00:00:00Z",
            last_used_start_at="2026-01-03T00:00:00Z",
            last_used_end_at="2026-02-03T00:00:00Z",
            sort="used_bytes",
            order="asc",
            limit=5,
            cursor="cursor",
        ),
    )
    request = urlsplit(paths[0])
    assert request.path == "/volumes/search"
    assert parse_qs(request.query) == {
        "q": ["data"],
        "name": ["data"],
        "name_prefix": ["da"],
        "type": ["disk,bucket"],
        "status": ["ready,error"],
        "imported": ["false"],
        "created_start_at": ["2026-01-01T00:00:00Z"],
        "created_end_at": ["2026-02-01T00:00:00Z"],
        "updated_start_at": ["2026-01-02T00:00:00Z"],
        "updated_end_at": ["2026-02-02T00:00:00Z"],
        "last_used_start_at": ["2026-01-03T00:00:00Z"],
        "last_used_end_at": ["2026-02-03T00:00:00Z"],
        "sort": ["used_bytes"],
        "order": ["asc"],
        "cursor": ["cursor"],
        "limit": ["5"],
    }
    assert (page.items[0].id, page.next_cursor, page.total, page.total_relation) == (
        "vol_1",
        None,
        1,
        "gte",
    )


@pytest.mark.parametrize(
    "field",
    [
        "q",
        "name",
        "name_prefix",
        "allowed_domain",
        "created_start_at",
        "created_end_at",
        "updated_start_at",
        "updated_end_at",
        "sort",
        "order",
        "cursor",
        "limit",
    ],
)
async def test_secret_search_omits_each_none_option(field: str) -> None:
    body = {"secrets": [], "next_cursor": None, "total": 0, "total_relation": "eq"}
    _, paths = await _run(
        [PlannedResponse(200, body)],
        lambda v2: v2.secrets.search(**{field: None}),  # type: ignore[arg-type]
    )
    assert paths == ["/secrets/search"]


@pytest.mark.parametrize(
    "field",
    [
        "q",
        "name",
        "name_prefix",
        "types",
        "statuses",
        "imported",
        "created_start_at",
        "created_end_at",
        "updated_start_at",
        "updated_end_at",
        "last_used_start_at",
        "last_used_end_at",
        "sort",
        "order",
        "cursor",
        "limit",
    ],
)
async def test_volume_search_omits_each_none_option(field: str) -> None:
    body = {"volumes": [], "next_cursor": None, "total": 0, "total_relation": "eq"}
    _, paths = await _run(
        [PlannedResponse(200, body)],
        lambda v2: v2.volumes.search(**{field: None}),  # type: ignore[arg-type]
    )
    assert paths == ["/volumes/search"]


async def test_volume_search_invalid_type_uses_the_shared_sdk_wording() -> None:
    error, paths = await _run([], lambda v2: v2.volumes.search(types=["disk", "other"]))  # type: ignore[list-item]
    assert paths == []
    assert isinstance(error, BadRequestError)
    assert str(error) == "types entries must be one of disk, bucket"


@pytest.mark.parametrize("name", ["secrets", "volumes"])
async def test_a_200_non_page_from_an_older_item_route_falls_back(name: str) -> None:
    case = _CASES[name]
    responses = [
        PlannedResponse(200, case.item),
        PlannedResponse(200, {case.field: [case.item], "next_cursor": None}),
    ]
    page, paths = await _run(responses, case.call)
    assert paths == [case.search_path + case.query, case.list_path + case.query]
    assert [_identity(item) for item in page.items] == [case.identity]


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


@pytest.mark.parametrize(
    "call",
    [
        lambda v2: v2.secrets.get("search"),
        lambda v2: v2.secrets.set("search", "value", allowed_domains=["example.com"]),
        lambda v2: v2.secrets.delete("search"),
        lambda v2: v2.volumes.get("search"),
        lambda v2: v2.volumes.create(name="search"),
        lambda v2: v2.volumes.delete("search"),
    ],
)
async def test_search_is_refused_as_a_secret_or_volume_name(call: Call) -> None:
    error, paths = await _run([], call)
    assert paths == []
    assert isinstance(error, BadRequestError)
    assert (error.status_code, error.code, error.source) == (400, "BAD_REQUEST", "SDK")
    assert "reserved for the search route" in str(error)


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


class _ScriptedTransport:
    """``send`` answers each request spec from its own queue of outcomes (a
    payload, or a ``DaytonaError`` to raise) and records the order of calls."""

    def __init__(self, outcomes: dict[str, list[dict[str, str] | DaytonaError]]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def send(self, spec: str) -> dict[str, str]:
        self.calls.append(spec)
        outcome = self.outcomes[spec].pop(0)
        if isinstance(outcome, DaytonaError):
            raise outcome
        return outcome


async def test_a_remembered_fallback_answering_410_gone_retries_search_once() -> None:
    """A long-lived client that saw a search-off deployment recovers once the
    deployment serves its lists from search: the list route's 410 ``GONE``
    forgets the probe and search answers; later calls stay on search."""
    off = DaytonaError("x", status_code=501, code="SEARCH_NOT_CONFIGURED")
    gone = DaytonaError("x", status_code=410, code="GONE")
    t = _ScriptedTransport({"search": [off, {"page": "s1"}, {"page": "s2"}], "list": [{"page": "l1"}, gone]})
    probe = _SearchProbe()
    assert await _search_or_list(t, "search", "list", probe) == {"page": "l1"}  # type: ignore[arg-type]
    assert probe.not_served
    # The switch: the remembered list route answers 410, search answers.
    assert await _search_or_list(t, "search", "list", probe) == {"page": "s1"}  # type: ignore[arg-type]
    assert not probe.not_served
    assert await _search_or_list(t, "search", "list", probe) == {"page": "s2"}  # type: ignore[arg-type]
    assert t.calls == ["search", "list", "list", "search", "search"]


async def test_a_410_from_the_list_right_after_a_search_miss_is_raised() -> None:
    """Search just said 501 and the list says 410 in the SAME call: no loop,
    the 410 is the answer."""
    off = DaytonaError("x", status_code=501, code="SEARCH_NOT_CONFIGURED")
    gone = DaytonaError("x", status_code=410, code="GONE")
    t = _ScriptedTransport({"search": [off], "list": [gone]})
    with pytest.raises(DaytonaError) as raised:
        await _search_or_list(t, "search", "list", _SearchProbe())  # type: ignore[arg-type]
    assert raised.value.status_code == 410
    assert t.calls == ["search", "list"]
