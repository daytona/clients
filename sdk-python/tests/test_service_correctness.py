# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Region-filtered name lookup, label and name Unicode checks, volume
get-or-create under a racing create, and the current API key projection.
The TypeScript twin is ``service-correctness.test.ts``."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Optional

import anyio
import pytest
from _handles import lazy_sandbox
from test_core import (
    _CURRENT_API_KEY_PAYLOAD,
    _ORGANIZATION_LIMITS_PAYLOAD,
    RecordingTransport,
    _sandbox_page_payload,
    _volume_payload,
    bind_api,
)

from daytona.v2 import BadRequestError, ConflictError, DaytonaError, NotFoundError
from daytona.v2 import services as services_module
from daytona.v2._core import RequestSpec
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.errors import create_error
from daytona.v2.services import AsyncVolumesService
from daytona.v2.types import DaytonaConfig

_KEY_ORG_ID = _CURRENT_API_KEY_PAYLOAD["context"]["org_id"]  # type: ignore[index]

_EMPTY_PAGE = {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": None}


def _client(responses: list[object]) -> tuple[AsyncDaytona, RecordingTransport]:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    api = RecordingTransport(responses)
    bind_api(client, api)
    return client, api


# --- find_by_name(region=...) filters by region -----------------------------


@pytest.mark.anyio
async def test_find_by_name_with_a_region_filters_the_search_by_it() -> None:
    page = {
        "sandboxes": [_sandbox_page_payload("sbx", "running", name="n", region="eu")],
        "total": 1,
        "total_relation": "eq",
    }
    client, api = _client([page])

    found = await client.find_by_name("n", region="eu")

    assert [sandbox.id for sandbox in found] == ["sbx"]
    assert api.specs[0].params == {
        "labels": json.dumps({"name": "n"}),
        "limit": 100,
        "region_ids": "eu",
    }
    assert api.specs[0].headers == {"x-daytona-region": "eu"}


@pytest.mark.anyio
@pytest.mark.parametrize("region", [None, "", "  ", "local", " LOCAL "])
async def test_find_by_name_without_a_region_searches_every_region(region: Optional[str]) -> None:
    client, api = _client([_EMPTY_PAGE])

    assert await client.find_by_name("n", region=region) == ()

    assert "region_ids" not in api.specs[0].params
    assert "x-daytona-region" not in api.specs[0].headers


@pytest.mark.anyio
async def test_find_by_name_keeps_the_region_filter_on_every_page() -> None:
    first = {
        "sandboxes": [_sandbox_page_payload("a", "running", region="eu")],
        "next_cursor": "c1",
        "total": 2,
        "total_relation": "eq",
    }
    second = {
        "sandboxes": [_sandbox_page_payload("b", "running", region="eu")],
        "total": 2,
        "total_relation": "eq",
    }
    client, api = _client([first, second])

    assert [sandbox.id for sandbox in await client.find_by_name("n", region="eu")] == ["a", "b"]
    assert [spec.params.get("region_ids") for spec in api.specs] == ["eu", "eu"]
    assert api.specs[1].params["cursor"] == "c1"


@pytest.mark.anyio
async def test_find_by_name_refuses_a_region_that_names_several() -> None:
    client, api = _client([])

    with pytest.raises(BadRequestError, match="^region entries must be non-empty strings without commas$"):
        await client.find_by_name("n", region="eu,us")
    assert api.specs == []


# --- labels and names hold no lone surrogate --------------------------------

_Call = Callable[[AsyncDaytona], Awaitable[object]]
_SURROGATE_CASES: list[tuple[str, _Call, str]] = [
    (
        "search label value",
        lambda d: d.search(labels={"k": "\ud800"}),
        "label values must not contain a lone surrogate",
    ),
    (
        "search label key",
        lambda d: d.search(labels={"\udfff": "v"}),
        "label keys must not contain a lone surrogate",
    ),
    ("find_by_name name", lambda d: d.find_by_name("a\ud800"), "name must not contain a lone surrogate"),
    (
        "create name",
        lambda d: d.create("img", name="a\ud800", wait_seconds=0),
        "name must not contain a lone surrogate",
    ),
    (
        "create label value",
        lambda d: d.create("img", labels={"k": "\ud800"}, wait_seconds=0),
        "label values must not contain a lone surrogate",
    ),
    (
        "create label key",
        lambda d: d.create("img", labels={"\ud800": "v"}, wait_seconds=0),
        "label keys must not contain a lone surrogate",
    ),
    (
        "set_labels value",
        lambda d: lazy_sandbox(d, "src").set_labels({"name": "n", "k": "\ud800"}),
        "label values must not contain a lone surrogate",
    ),
    (
        "fork name",
        lambda d: lazy_sandbox(d, "src").fork(name="\ud800", wait_seconds=0),
        "fork name must not contain a lone surrogate",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("case", "call", "message"), _SURROGATE_CASES, ids=[case[0] for case in _SURROGATE_CASES]
)
async def test_lone_surrogates_in_labels_and_names_are_refused_before_any_request(
    case: str, call: _Call, message: str
) -> None:
    del case
    client, api = _client([])

    with pytest.raises(BadRequestError, match=f"^{message}$") as raised:
        await call(client)

    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert api.specs == []


@pytest.mark.anyio
async def test_an_empty_label_key_is_reported_before_a_surrogate() -> None:
    client, _ = _client([])

    with pytest.raises(BadRequestError, match="^label keys must not be empty$"):
        await client.search(labels={"k": "\ud800", "": "v"})


@pytest.mark.anyio
async def test_astral_characters_in_labels_and_names_are_sent() -> None:
    client, api = _client([_EMPTY_PAGE, _EMPTY_PAGE])

    await client.search(labels={"k": "\U0001f600"})
    await client.find_by_name("\U0001f600")

    assert json.loads(str(api.specs[0].params["labels"])) == {"k": "\U0001f600"}
    assert json.loads(str(api.specs[1].params["labels"])) == {"name": "\U0001f600"}


# --- volumes.get(create=True) under a racing create --------------------------


def _missing() -> DaytonaError:
    return create_error("volume not found", 404, "NOT_FOUND", "DAYTONA_API")


def _name_taken() -> DaytonaError:
    return create_error("a volume with that name already exists", 409, "CONFLICT", "DAYTONA_API")


@pytest.fixture
def no_reread_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services_module, "_VOLUME_CONFLICT_REREAD_DELAYS", (0.0, 0.0, 0.0))


@pytest.mark.anyio
async def test_volume_get_or_create_reads_the_volume_a_racing_create_made() -> None:
    transport = RecordingTransport([_missing(), _name_taken(), _volume_payload("data")])
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    volume = await volumes.get("data", create=True)

    assert volume.id == "data"
    assert [(spec.method, spec.path, spec.params) for spec in transport.specs] == [
        ("GET", "/volumes/data", {}),
        ("POST", "/volumes", {}),
        ("GET", "/volumes/data", {}),
    ]


@pytest.mark.anyio
async def test_volume_get_or_create_rereads_until_the_volume_is_readable(no_reread_pauses: None) -> None:
    del no_reread_pauses
    transport = RecordingTransport(
        [_missing(), _name_taken(), _missing(), _missing(), _volume_payload("data")]
    )
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    assert (await volumes.get("data", create=True)).id == "data"
    assert len(transport.specs) == 5


@pytest.mark.anyio
async def test_volume_get_or_create_raises_the_conflict_when_the_volume_never_appears(
    no_reread_pauses: None,
) -> None:
    del no_reread_pauses
    transport = RecordingTransport([_missing(), _name_taken(), _missing(), _missing(), _missing()])
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    with pytest.raises(ConflictError) as raised:
        await volumes.get("data", create=True)

    assert raised.value.code == "CONFLICT"
    assert [spec.method for spec in transport.specs] == ["GET", "POST", "GET", "GET", "GET"]


@pytest.mark.anyio
async def test_volume_get_or_create_raises_other_conflicts_unchanged() -> None:
    other = create_error("region not empty", 409, "REGION_NOT_EMPTY", "DAYTONA_API")
    transport = RecordingTransport([_missing(), other])
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    with pytest.raises(ConflictError) as raised:
        await volumes.get("data", create=True)

    assert raised.value is other
    assert [spec.method for spec in transport.specs] == ["GET", "POST"]


@pytest.mark.anyio
async def test_volume_get_or_create_raises_a_conflict_code_without_status_409_unchanged() -> None:
    odd = create_error("conflict", 500, "CONFLICT", "DAYTONA_API")
    transport = RecordingTransport([_missing(), odd])
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    with pytest.raises(DaytonaError) as raised:
        await volumes.get("data", create=True)

    assert raised.value is odd
    assert [spec.method for spec in transport.specs] == ["GET", "POST"]


@pytest.mark.anyio
async def test_volume_get_or_create_raises_a_failed_reread() -> None:
    denied = create_error("forbidden", 403, "FORBIDDEN", "DAYTONA_API")
    transport = RecordingTransport([_missing(), _name_taken(), denied])
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    with pytest.raises(DaytonaError) as raised:
        await volumes.get("data", create=True)
    assert raised.value is denied


@pytest.mark.anyio
async def test_volume_get_without_create_raises_a_conflict_free_not_found() -> None:
    transport = RecordingTransport([_missing()])
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        await volumes.get("data")
    assert [spec.method for spec in transport.specs] == ["GET"]


class _RacingVolumes:
    """A volume API where every caller reads before any create lands: the
    first create wins, every later one is the name conflict."""

    request_timeout_seconds = 30.0

    def __init__(self, callers: int) -> None:
        self.volumes: dict[str, object] = {}
        self.creates = 0
        self._readers = 0
        self._callers = callers
        self._all_read = anyio.Event()

    async def send(self, spec: RequestSpec) -> object:
        if spec.method == "GET":
            name = spec.path.removeprefix("/volumes/")
            if name in self.volumes:
                return self.volumes[name]
            self._readers += 1
            if self._readers >= self._callers:
                self._all_read.set()
            raise _missing()
        await self._all_read.wait()
        self.creates += 1
        body = spec.json_body
        assert isinstance(body, dict)
        name = str(body["name"])
        if name in self.volumes:
            raise _name_taken()
        self.volumes[name] = _volume_payload(name)
        return self.volumes[name]


@pytest.mark.anyio
async def test_concurrent_get_or_create_calls_all_return_the_one_volume() -> None:
    callers = 6
    transport = _RacingVolumes(callers)
    volumes = AsyncVolumesService(transport)  # type: ignore[arg-type]
    results: list[str] = []

    async def get_or_create() -> None:
        results.append((await volumes.get("shared", create=True)).id)

    async with anyio.create_task_group() as group:
        for _ in range(callers):
            group.start_soon(get_or_create)

    assert results == ["shared"] * callers
    assert transport.creates == callers
    assert list(transport.volumes) == ["shared"]


# --- current_api_key / get_limits ignore the unread sync_to_v0 ---------------


@pytest.mark.anyio
@pytest.mark.parametrize("sync_to_v0", ["absent", None, "yes"])
async def test_current_api_key_ignores_the_key_sync_flag(sync_to_v0: object) -> None:
    payload = {key: value for key, value in _CURRENT_API_KEY_PAYLOAD.items() if key != "sync_to_v0"}
    if sync_to_v0 != "absent":
        payload["sync_to_v0"] = sync_to_v0
    client, _ = _client([payload])

    key = await client.current_api_key()

    assert key.id == _CURRENT_API_KEY_PAYLOAD["id"]
    assert key.context.org_id == _KEY_ORG_ID


@pytest.mark.anyio
async def test_get_limits_looks_up_the_key_organization_without_the_key_sync_flag() -> None:
    payload = {key: value for key, value in _CURRENT_API_KEY_PAYLOAD.items() if key != "sync_to_v0"}
    client, api = _client([payload, _ORGANIZATION_LIMITS_PAYLOAD])

    limits = await client.get_limits()

    assert limits.sandboxes.concurrent.limit == 100
    assert [spec.path for spec in api.specs] == [
        "/api-keys/current",
        f"/orgs/{_KEY_ORG_ID}/limits",
    ]


@pytest.mark.anyio
async def test_current_api_key_still_checks_the_fields_it_returns() -> None:
    for broken, message in (
        ({"name": None}, "expected current API key response: name is required"),
        (
            {"created_at": "2026-01-01"},
            "current API key response has a created_at that is not an RFC 3339 timestamp",
        ),
        (
            {"context": {"org_id": "o", "org_name": "o"}},
            "expected current API key response: context.project_name is required",
        ),
    ):
        client, _ = _client([{**_CURRENT_API_KEY_PAYLOAD, **broken}])
        with pytest.raises(BadRequestError, match=f"^{re.escape(message)}$"):
            await client.current_api_key()
