# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The argument rule for the client, lifecycle and platform-service area.

A wrong type or value is a :class:`BadRequestError` (400, ``BAD_REQUEST``,
``SDK``) before any request. The TypeScript twin is
``tests/argument-checks.test.ts``.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timedelta, timezone, tzinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import (
    BadRequestError,
    DaytonaError,
    ImageBuilder,
    PortMapping,
    RequestTimeoutError,
    Resources,
    VolumeMount,
)
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
from daytona.v2.errors import create_error
from daytona.v2.types import Image, JsonObject

pytestmark = pytest.mark.anyio

Call = Callable[[AsyncDaytonaV2, str], Awaitable[object]]

_REFUSED: list[tuple[str, Call]] = [
    ("wait until a non-state", lambda d, _url: lazy_sandbox(d, "sbx").wait(until="bogus")),  # type: ignore[arg-type]
    (
        "wait until a list with a non-state",
        lambda d, _url: lazy_sandbox(d, "sbx").wait(until=["started", "bogus"]),  # type: ignore[list-item]
    ),
    ("wait until a number", lambda d, _url: lazy_sandbox(d, "sbx").wait(until=5)),  # type: ignore[arg-type]
    # Only an omitted `until` defaults to `started`; TypeScript refuses `until: null` too.
    ("wait until None", lambda d, _url: lazy_sandbox(d, "sbx").wait(until=None)),  # type: ignore[arg-type]
    ("wait until an empty list", lambda d, _url: lazy_sandbox(d, "sbx").wait(until=[])),
    (
        "docker timeout as a string",
        lambda d, url: lazy_sandbox(d, "sbx", toolbox_url=url, access_token="tok").docker.wait_until_ready(
            timeout="5"  # type: ignore[arg-type]
        ),
    ),
    (
        "docker timeout None",
        lambda d, url: lazy_sandbox(d, "sbx", toolbox_url=url, access_token="tok").docker.wait_until_ready(
            timeout=None  # type: ignore[arg-type]
        ),
    ),
    ("stop(123)", lambda d, _url: d.stop(123)),  # type: ignore[arg-type]
    ("delete(None)", lambda d, _url: d.delete(None)),  # type: ignore[arg-type]
    ("start(blank id)", lambda d, _url: d.start("  ")),
    ("create region as a number", lambda d, _url: d.create(region=5)),  # type: ignore[arg-type]
    ("find_by_name region as a number", lambda d, _url: d.find_by_name("x", region=5)),  # type: ignore[arg-type]
    ("create wait_seconds None", lambda d, _url: d.create(wait_seconds=None)),  # type: ignore[arg-type]
    ("fork wait_seconds None", lambda d, _url: lazy_sandbox(d, "sbx").fork(wait_seconds=None)),  # type: ignore[arg-type]
    ("memory 0", lambda d, _url: d.create(resources=Resources(memory=0))),
    ("disk -1", lambda d, _url: d.create(resources=Resources(disk=-1))),
    ("memory_max 0", lambda d, _url: d.create(resources=Resources(memory_max=0))),
    ("disk_max 0", lambda d, _url: d.create(resources=Resources(disk_max=0))),
    ("get(blank id)", lambda d, _url: d.get("  ")),
    # No reservation option is silently dropped from the body.
    ("reservations.create region as a number", lambda d, _url: d.reservations.create(region=5)),  # type: ignore[arg-type]
    (
        "reservations.create model as an object",
        lambda d, _url: d.reservations.create(region="r", model=object()),
    ),  # type: ignore[arg-type]
    (
        "reservations.create auto_renew as a string",
        lambda d, _url: d.reservations.create(region="r", auto_renew="yes"),
    ),  # type: ignore[arg-type]
    (
        "reservations.create_bulk count as a string",
        lambda d, _url: d.reservations.create_bulk(region="r", count="2"),
    ),  # type: ignore[arg-type]
    (
        "reservations.create_bulk min_count 0",
        lambda d, _url: d.reservations.create_bulk(region="r", count=2, min_count=0),
    ),
    ("reservations.create_bulk count 0", lambda d, _url: d.reservations.create_bulk(region="r", count=0)),
    ("reservations.create_bulk count 101", lambda d, _url: d.reservations.create_bulk(region="r", count=101)),
    (
        "reservations.create_bulk min_count above count",
        lambda d, _url: d.reservations.create_bulk(region="r", count=2, min_count=3),
    ),
    (
        "create_access_token ports a number",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(ports=3000),  # type: ignore[arg-type]
    ),
    (
        "create_access_token ports with a string",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(ports=["3000"]),  # type: ignore[list-item]
    ),
    # The sandbox daemon's ports are never preview ports (the TypeScript twin
    # refuses the same calls).
    (
        "create_access_token reserved port 2280",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(ports=[3000, 2280]),
    ),
    (
        "create_access_token reserved port 2281",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(ports=[2281]),
    ),
    (
        "create_access_token reserved port 22220",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(ports=[22220]),
    ),
    ("ports.expose reserved port", lambda d, _url: lazy_sandbox(d, "sbx").ports.expose(2280)),
    ("ports.preview_link reserved port", lambda d, _url: lazy_sandbox(d, "sbx").ports.preview_link(2281)),
    (
        "ports.signed_preview_link reserved port",
        lambda d, _url: lazy_sandbox(d, "sbx").ports.signed_preview_link(22220),
    ),
    ("set_labels empty key", lambda d, _url: lazy_sandbox(d, "sbx").set_labels({"team": "a", "": "x"})),
    ("create labels empty key", lambda d, _url: d.create(labels={"": "x"}, wait_seconds=0)),
    (
        "create_from_snapshot labels empty key",
        lambda d, _url: d.create_from_snapshot("snap", labels={"": "x"}, wait_seconds=0),
    ),
    (
        "create_macos labels empty key",
        lambda d, _url: d.create_macos("img", labels={"": "x"}, wait_seconds=0),
    ),
    (
        "create_from_build labels empty key",
        lambda d, _url: d.create_from_build(
            ImageBuilder.from_registry("python:3.12"), labels={"": "x"}, wait_seconds=0
        ),
    ),
    ("search labels empty key", lambda d, _url: d.search(labels={"team": "a", "": "x"})),
    ("create create_timeout_seconds above 2**53-1", lambda d, _url: d.create(create_timeout_seconds=2**53)),
    ("create create_timeout_seconds 10**400", lambda d, _url: d.create(create_timeout_seconds=10**400)),
    ("create memory above int32 MiB", lambda d, _url: d.create(resources=Resources(memory=2_097_152))),
    ("create disk_max above int32 MiB", lambda d, _url: d.create(resources=Resources(disk_max=2_097_152))),
    ("create cpu 10**400", lambda d, _url: d.create(resources=Resources(cpu=10**400))),
    # Argument types the SDK used to forward: the TypeScript twin pins the same rows.
    (
        "secrets.set if_not_exists a string",
        lambda d, _url: d.secrets.set("K", "v", allowed_domains=["*"], if_not_exists="false"),
    ),  # type: ignore[arg-type]
    ("secrets.set value a number", lambda d, _url: d.secrets.set("K", 5, allowed_domains=["*"])),  # type: ignore[arg-type]
    (
        "secrets.set description a number",
        lambda d, _url: d.secrets.set("K", "v", allowed_domains=["*"], description=5),
    ),  # type: ignore[arg-type]
    ("images.pull wait a string", lambda d, _url: d.images.pull("alpine", wait="x")),  # type: ignore[arg-type]
    ("images.pull cpu_arch bogus", lambda d, _url: d.images.pull("alpine", cpu_arch="bogus")),  # type: ignore[arg-type]
    (
        "images.build idle_expiry_seconds 0",
        lambda d, _url: d.images.build(ImageBuilder.from_registry("alpine"), idle_expiry_seconds=0),
    ),
    (
        "images.build idle_expiry_seconds -2",
        lambda d, _url: d.images.build(ImageBuilder.from_registry("alpine"), idle_expiry_seconds=-2),
    ),
    ("list limit a string", lambda d, _url: d.list(limit="x")),  # type: ignore[arg-type]
    ("list limit 0", lambda d, _url: d.list(limit=0)),
    ("list limit 201", lambda d, _url: d.list(limit=201)),
    ("list cursor a number", lambda d, _url: d.list(cursor=5)),  # type: ignore[arg-type]
    ("search name_prefix a number", lambda d, _url: d.search(name_prefix=5)),  # type: ignore[arg-type]
    ("search limit a string", lambda d, _url: d.search(limit="x")),  # type: ignore[arg-type]
    ("search min_cores a string", lambda d, _url: d.search(min_cores="x")),  # type: ignore[arg-type]
    ("search min_memory_mib 1.5", lambda d, _url: d.search(min_memory_mib=1.5)),  # type: ignore[arg-type]
    ("search spot a string", lambda d, _url: d.search(spot="yes")),  # type: ignore[arg-type]
    ("search sort bogus", lambda d, _url: d.search(sort="x")),  # type: ignore[arg-type]
    ("search order bogus", lambda d, _url: d.search(order="up")),  # type: ignore[arg-type]
    ("images.list limit a string", lambda d, _url: d.images.list(limit="x")),  # type: ignore[arg-type]
    ("images.list name a number", lambda d, _url: d.images.list(name=5)),  # type: ignore[arg-type]
    ("images.list anonymous None", lambda d, _url: d.images.list(anonymous=None)),  # type: ignore[arg-type]
    ("images.list platform bogus", lambda d, _url: d.images.list(platform="yes")),  # type: ignore[arg-type]
    ("snapshots.list name a number", lambda d, _url: d.snapshots.list(name=5)),  # type: ignore[arg-type]
    ("secrets.list limit -1", lambda d, _url: d.secrets.list(limit=-1)),
    ("volumes.list cursor a number", lambda d, _url: d.volumes.list(cursor=5)),  # type: ignore[arg-type]
    ("reservations.list limit 0", lambda d, _url: d.reservations.list(limit=0)),
    ("ports.list_endpoints limit 0", lambda d, _url: lazy_sandbox(d, "sbx").ports.list_endpoints(limit=0)),
    # A lone surrogate in a query value, and a header value outside printable
    # ASCII (the TypeScript twin pins the same rows).
    ("list cursor with a lone surrogate", lambda d, _url: d.list(cursor="a\ud800b")),
    ("mac_models.catalog region a number", lambda d, _url: d.mac_models.catalog(region=5)),  # type: ignore[arg-type]
    ("mac_models.catalog region a list", lambda d, _url: d.mac_models.catalog(region=["a", "b"])),  # type: ignore[arg-type]
    (
        "mac_models.catalog region with a lone surrogate",
        lambda d, _url: d.mac_models.catalog(region="a\ud800b"),
    ),
    ("volumes.get create a string", lambda d, _url: d.volumes.get("v", create="false")),  # type: ignore[arg-type]
    ("volumes.get create a list", lambda d, _url: d.volumes.get("v", create=[])),  # type: ignore[arg-type]
    ("volumes.get id_only a string", lambda d, _url: d.volumes.get("v", id_only="no")),  # type: ignore[arg-type]
    ("volumes.get create None", lambda d, _url: d.volumes.get("v", create=None)),  # type: ignore[arg-type]
    ("search sandbox_class with a lone surrogate", lambda d, _url: d.search(sandbox_class="a\ud800b")),  # type: ignore[arg-type]
    ("search region with a lone surrogate", lambda d, _url: d.search(region="a\ud800b")),
    (
        "search created_start_at with a lone surrogate",
        lambda d, _url: d.search(created_start_at="2026-01-01T00:00:00Z\ud800"),
    ),
    ("search region a number", lambda d, _url: d.search(region=5)),  # type: ignore[arg-type]
    ("search region a dict", lambda d, _url: d.search(region={"r": "x"})),  # type: ignore[arg-type]
    ("search region [1]", lambda d, _url: d.search(region=[1])),  # type: ignore[list-item]
    ("search sandbox_class a number", lambda d, _url: d.search(sandbox_class=5)),  # type: ignore[arg-type]
    ("search sandbox_class a dict", lambda d, _url: d.search(sandbox_class={"linux": "x"})),  # type: ignore[arg-type]
    ("search sandbox_class [1]", lambda d, _url: d.search(sandbox_class=[1])),  # type: ignore[list-item]
    ("search sandbox_class bogus", lambda d, _url: d.search(sandbox_class="bogus")),  # type: ignore[arg-type]
    ("search created_start_at not RFC 3339", lambda d, _url: d.search(created_start_at="yesterday")),
    ("volumes.create name U+001C", lambda d, _url: d.volumes.create(name="\u001c")),
    # The contract's size_gib is an int32 (twin in argument-checks.test.ts).
    ("volumes.create size_gib above int32", lambda d, _url: d.volumes.create(name="v", size_gib=2**31)),
    # The API rejects an empty allowed_domains (twin in argument-checks.test.ts).
    ("secrets.set allowed_domains empty", lambda d, _url: d.secrets.set("K", "v", allowed_domains=[])),
    (
        "create volumes[].volume_id U+001C",
        lambda d, _url: d.create(volumes=[VolumeMount(volume_id="\u001c", mount_path="/m")]),
    ),
    (
        "follow_build_logs cursor a number",
        lambda d, _url: d.images.follow_build_logs("img", on_logs=print, cursor=5),
    ),  # type: ignore[arg-type]
    (
        "follow_build_logs cursor with a tab",
        lambda d, _url: d.images.follow_build_logs("img", on_logs=print, cursor="a\tb"),
    ),
    (
        "follow_build_logs cursor empty",
        lambda d, _url: d.images.follow_build_logs("img", on_logs=print, cursor=""),
    ),
    (
        "sandbox follow_build_logs cursor with DEL",
        lambda d, _url: lazy_sandbox(d, "sbx").follow_build_logs(on_logs=print, cursor="a\x7fb"),
    ),
    ("search name_prefix with a lone surrogate", lambda d, _url: d.search(name_prefix="a\ud800b")),
    ("images.list name with a lone surrogate", lambda d, _url: d.images.list(name="a\ud800b")),
    ("create idempotency_key non-ASCII", lambda d, _url: d.create(idempotency_key="k\u00e9y")),
    ("create region non-ASCII", lambda d, _url: d.create(region="r\u00e9gion")),
    ("find_by_name region with a newline", lambda d, _url: d.find_by_name("x", region="a\nb")),
    (
        "reservations.renew idempotency_key non-ASCII",
        lambda d, _url: d.reservations.renew("res", idempotency_key="k\u00e9y"),
    ),
    (
        "reset_network_policy if_match non-ASCII",
        lambda d, _url: lazy_sandbox(d, "sbx").reset_network_policy(if_match="\u00e9"),
    ),
    ("create memory 10**400", lambda d, _url: d.create(resources=Resources(memory=10**400))),
    ("docker timeout 10**309 is no limit, not an overflow", lambda d, url: _docker_refused_type(d, url)),
    (
        "reservations.update auto_renew as a string",
        lambda d, _url: d.reservations.update("res", auto_renew="yes"),
    ),  # type: ignore[arg-type]
    ("volumes.delete(non-string)", lambda d, _url: d.volumes.delete(object())),  # type: ignore[arg-type]
    ("volumes.delete(blank)", lambda d, _url: d.volumes.delete(" ")),
    (
        "ports.expose public as a string",
        lambda d, _url: lazy_sandbox(d, "sbx").ports.expose(8080, public="false"),
    ),  # type: ignore[arg-type]
    (
        "create_access_token operations not a list",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(operations="toolbox"),
    ),
    (
        "create_access_token operations with a number",
        lambda d, _url: lazy_sandbox(d, "sbx").create_access_token(operations=[1]),  # type: ignore[list-item]
    ),
    ("ports proto sctp", lambda d, _url: d.create(ports=[PortMapping(port=3000, proto="sctp")])),
    (
        "create_from_build idle_expiry_seconds as a string",
        lambda d, _url: d.create_from_build(ImageBuilder.from_registry("alpine"), idle_expiry_seconds="60"),  # type: ignore[arg-type]
    ),
    (
        "images.build cpu_arch as a number",
        lambda d, _url: d.images.build(ImageBuilder.from_registry("alpine"), cpu_arch=5),  # type: ignore[arg-type]
    ),
    (
        "capture_image idle_expiry_seconds 1.5",
        lambda d, _url: lazy_sandbox(d, "sbx").capture_image(idle_expiry_seconds=1.5),
    ),  # type: ignore[arg-type]
]


async def _docker_refused_type(d: AsyncDaytonaV2, url: str) -> object:
    # An integer too large for a float is accepted (no limit) without an
    # OverflowError; the refusal then comes from the mistyped value after it.
    from daytona.v2._core import wait_budget_seconds

    assert wait_budget_seconds(10**309) == 0.0
    return await lazy_sandbox(d, "sbx", toolbox_url=url, access_token="tok").docker.wait_until_ready(
        timeout=[1]
    )  # type: ignore[arg-type]


def _assert_server_stop(error: DaytonaError) -> None:
    """The fake's own 500 ({"message": "stop"}, which names no source) ended the
    call: the request was sent, so it is not the SDK's refusal."""
    assert (error.status_code, error.source, str(error)) == (500, None, "stop")


@pytest.fixture
def api() -> Iterator[FakeApi]:
    with FakeApi([]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        yield fake


@pytest.mark.parametrize(("name", "call"), _REFUSED, ids=[case[0] for case in _REFUSED])
async def test_a_wrong_argument_is_refused_before_any_request(api: FakeApi, name: str, call: Call) -> None:
    del name
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError) as raised:
            await call(root.v2, api.url)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert api.requests == []


async def _create_body(name: str) -> tuple[JsonObject, str]:
    with FakeApi([PlannedResponse(200, {"id": "sbx", "status": "running"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.create(name=name, wait_seconds=0)
    request = fake.requests[0]
    body = json.loads(request.body)
    assert isinstance(body, dict)
    return body, request.headers["Idempotency-Key"]


async def test_create_timeout_seconds_none_is_omitted() -> None:
    with FakeApi([PlannedResponse(200, {"id": "sbx", "status": "running"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.create(create_timeout_seconds=None, wait_seconds=0)
    assert "create_timeout_seconds" not in json.loads(fake.requests[0].body)


# Python's str.isspace() set: U+001C and U+0085 are whitespace here and not to
# JavaScript's trim(); U+FEFF is the reverse. TypeScript follows this set: a
# blank name is refused, any other is stamped.
async def test_a_blank_name_follows_python_whitespace() -> None:
    with pytest.raises(BadRequestError, match="^name must be a non-empty string when given$"):
        await _create_body("\x1c\x85")
    body, key = await _create_body("\ufeff")
    labels = body.get("labels")
    assert isinstance(labels, dict) and labels["name"] == "\ufeff"
    assert key.startswith("name")


async def test_a_server_408_during_the_build_wait_keeps_its_source(monkeypatch: pytest.MonkeyPatch) -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        images = root.v2.images
        building = Image(
            id="img",
            name=None,
            status="building",
            region="region-a",
            anonymous=True,
            retention="idle",
            raw_retention="idle",
            sandbox_class="linux",
            available_in=(),
            origin="dockerfile",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        async def build(*_args: object, **_kwargs: object) -> Image:
            return building

        async def get(*_args: object, **_kwargs: object) -> Image:
            await anyio.sleep(0.08)
            raise create_error("server timeout", 408, "REQUEST_TIMEOUT", "DAYTONA_API")

        monkeypatch.setattr(images, "build", build)
        monkeypatch.setattr(images, "get", get)
        with pytest.raises(RequestTimeoutError, match="server timeout") as raised:
            await root.v2.create_from_build(ImageBuilder.from_registry("alpine"), build_timeout=0.02)
    assert raised.value.source == "DAYTONA_API"


_IMAGE: JsonObject = {
    "id": "img",
    "class": "linux",
    "status": "building",
    "region_id": "region-a",
    "region_ids": ["region-a"],
    "anonymous": True,
    "retention": "idle",
    "origin": "registry",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "format_version": 2,
    "format_outdated": False,
    "platform": False,
    "default": False,
    "deprecated": False,
}


async def test_an_image_pull_pauses_between_early_non_terminal_answers() -> None:
    planned = [PlannedResponse(200, {"id": "img", "status": "pending"})] + [PlannedResponse(200, _IMAGE)] * 50
    with FakeApi(planned) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(RequestTimeoutError):
                await root.v2.images.pull("alpine", timeout=1.5)
    # The submission, the first read, then one hold about every second.
    assert len(fake.requests) <= 5


async def test_a_ready_wait_whose_first_hold_outlives_the_deadline_is_a_wait_timeout() -> None:
    listener = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []
    threading.Thread(target=lambda: accepted.append(listener.accept()[0]), daemon=True).start()
    os.environ["DAYTONA_V2_API_URL"] = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(RequestTimeoutError) as raised:
                await root.v2.images.get("img", wait="ready", timeout=0.05)
    finally:
        for connection in accepted:
            connection.close()
        listener.close()
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )


_RESERVATION: JsonObject = {
    "id": "res",
    "org_id": "o",
    "project_id": "p",
    "region_id": "r",
    "status": "active",
    "origin": "explicit",
    "auto_renew": True,
    "slots_used": 0,
    "period_n": 1,
    "period_seconds": 86400,
    "renewal_pending": False,
    "created_at": "2026-01-01T00:00:00Z",
    "resources_used": {"cores": 0, "memory_mib": 0, "disk_mib": 0},
}

_ALLOWED: list[tuple[str, Callable[[AsyncDaytonaV2], Awaitable[object]], JsonObject, JsonObject]] = [
    (
        "blank region and model",
        lambda d: d.reservations.create(region="", model=""),
        _RESERVATION,
        {"region_id": "", "model": ""},
    ),
    (
        "count 100, min_count = count",
        lambda d: d.reservations.create_bulk(region="r", count=100, min_count=100),
        {"requested": 100, "reserved": 0, "reservations": []},
        {"count": 100, "min_count": 100},
    ),
    (
        "count 1",
        lambda d: d.reservations.create_bulk(region=" ", count=1),
        {"requested": 1, "reserved": 0, "reservations": []},
        {"region_id": " ", "count": 1},
    ),
]


@pytest.mark.parametrize(
    ("name", "call", "response", "expected"), _ALLOWED, ids=[case[0] for case in _ALLOWED]
)
async def test_a_reservation_the_contract_allows_is_sent(
    name: str, call: Callable[[AsyncDaytonaV2], Awaitable[object]], response: JsonObject, expected: JsonObject
) -> None:
    del name
    with FakeApi([PlannedResponse(200, response)]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await call(root.v2)
    sent = json.loads(fake.requests[0].body)
    assert {key: sent[key] for key in expected} == expected


class _SlowCreateApi:
    """Answers every POST after ``delay`` seconds, recording each body."""

    def __init__(self, delay: float) -> None:
        self.bodies: list[bytes] = []
        stop = threading.Event()
        self.stop = stop
        bodies = self.bodies

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def do_POST(self) -> None:
                bodies.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                stop.wait(delay)
                data = json.dumps({"id": "sbx", "status": "running"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("route", ["create", "create_from_snapshot", "create_macos"])
@pytest.mark.parametrize("wait_seconds", [10**400, 2_147_484])
async def test_a_create_wait_above_the_timer_maximum_is_sent_capped_with_no_client_deadline(
    route: str, wait_seconds: int
) -> None:
    # No limit: the body carries the timer maximum, the same value TypeScript
    # sends, and the POST outlives a 1 s request timeout (answered after 1.5 s).
    api = _SlowCreateApi(1.5)
    os.environ["DAYTONA_V2_API_URL"] = api.url
    try:
        async with AsyncDaytona(DaytonaConfig(api_key="key", request_timeout=1)) as root:
            source = {"create": "img_1", "create_from_snapshot": "snap_1", "create_macos": "sonoma"}[route]
            sandbox = await getattr(root.v2, route)(source, wait_seconds=wait_seconds)
    finally:
        api.close()
    assert sandbox.id == "sbx"
    assert json.loads(api.bodies[0])["wait_seconds"] == 2_147_483


@pytest.mark.parametrize(
    "call",
    [
        lambda d: d.create_from_build(ImageBuilder.from_registry("python:3"), build_timeout=10**400),
        lambda d: d.create_from_build(ImageBuilder.from_registry("python:3"), wait_seconds=10**400),
        lambda d: d.images.follow_build_logs("img", on_logs=lambda _chunk: None, timeout=10**400),
    ],
    ids=["create_from_build build_timeout", "create_from_build wait_seconds", "follow_build_logs timeout"],
)
async def test_an_integer_too_large_for_a_float_is_no_limit_not_an_overflow(
    call: Callable[[AsyncDaytonaV2], Awaitable[object]],
) -> None:
    # The value passes as "no limit": the first request is sent, and the fake's
    # own 404 ends the call (not an OverflowError, a refusal or any other failure).
    with FakeApi([PlannedResponse(404, {"code": "NOT_FOUND", "message": "stop here"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(DaytonaError) as raised:
                await call(root.v2)
    assert (raised.value.status_code, raised.value.code, str(raised.value)) == (404, "NOT_FOUND", "stop here")
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    ("value", "finite"),
    [
        (2**1024 - 2**970 - 1, True),
        (-(2**1024 - 2**970 - 1), True),
        (-(2**1023) - 1, True),
        (2**1024 - 2**970, False),
        (-(2**1024 - 2**970), False),
        (10**400, False),
        (1.5, True),
        (float("inf"), False),
        (float("nan"), False),
        (True, False),
    ],
    ids=[
        "max",
        "-max",
        "-(2**1023)-1",
        "first-overflow",
        "-first-overflow",
        "10**400",
        "1.5",
        "inf",
        "nan",
        "bool",
    ],
)
def test_finite_number_is_exactly_what_float_converts(value: object, finite: bool) -> None:
    from daytona.v2._core import finite_number

    assert finite_number(value) is finite


@pytest.mark.parametrize("kind", ["images", "snapshots"])
@pytest.mark.parametrize("wait", [None, "ready"])
@pytest.mark.parametrize("timeout", [-1, float("nan"), "5"])
async def test_an_artifact_get_checks_its_timeout_with_or_without_wait(
    kind: str, wait: str | None, timeout: object
) -> None:
    with FakeApi([]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            service = root.v2.images if kind == "images" else root.v2.snapshots
            with pytest.raises(BadRequestError, match="timeout must be a non-negative finite number"):
                await service.get("art", wait=wait, timeout=timeout)  # type: ignore[arg-type]
    assert fake.requests == []


# Every id or name a caller puts into a request path in the platform services
# (the TypeScript twin pins the same calls): a string that is not blank by
# Python's whitespace set, refused with BadRequestError naming the argument.
_PATH_CALLS: list[tuple[str, str, Callable[[AsyncDaytonaV2, object], Awaitable[object]]]] = [
    ("images.get", "image_id", lambda d, v: d.images.get(v)),  # type: ignore[arg-type]
    ("images.get wait", "image_id", lambda d, v: d.images.get(v, wait="ready", timeout=1)),  # type: ignore[arg-type]
    ("images.delete", "image_id", lambda d, v: d.images.delete(v)),  # type: ignore[arg-type]
    ("images.promote id", "image_id", lambda d, v: d.images.promote(v, "named")),  # type: ignore[arg-type]
    ("images.promote name", "name", lambda d, v: d.images.promote("img", v)),  # type: ignore[arg-type]
    (
        "images.follow_build_logs",
        "image_id",
        lambda d, v: d.images.follow_build_logs(v, on_logs=lambda _chunk: None),  # type: ignore[arg-type]
    ),
    ("snapshots.get", "snapshot_id", lambda d, v: d.snapshots.get(v)),  # type: ignore[arg-type]
    ("snapshots.delete", "snapshot_id", lambda d, v: d.snapshots.delete(v)),  # type: ignore[arg-type]
    ("volumes.get", "name_or_id", lambda d, v: d.volumes.get(v)),  # type: ignore[arg-type]
    ("volumes.delete", "name_or_id", lambda d, v: d.volumes.delete(v)),  # type: ignore[arg-type]
    ("secrets.get", "name", lambda d, v: d.secrets.get(v)),  # type: ignore[arg-type]
    ("secrets.set", "name", lambda d, v: d.secrets.set(v, "value", allowed_domains=["*"])),  # type: ignore[arg-type]
    ("secrets.delete", "name", lambda d, v: d.secrets.delete(v)),  # type: ignore[arg-type]
    ("reservations.get", "reservation_id", lambda d, v: d.reservations.get(v)),  # type: ignore[arg-type]
    ("reservations.renew", "reservation_id", lambda d, v: d.reservations.renew(v)),  # type: ignore[arg-type]
    (
        "reservations.update",
        "reservation_id",
        lambda d, v: d.reservations.update(v, auto_renew=True),  # type: ignore[arg-type]
    ),
    ("get_limits", "organization_id", lambda d, v: d.get_limits(organization_id=v)),  # type: ignore[arg-type]
    ("create_snapshot", "name", lambda d, v: lazy_sandbox(d, "sbx").create_snapshot(v)),  # type: ignore[arg-type]
    (
        "sandbox.volumes.pull",
        "volume_id",
        lambda d, v: lazy_sandbox(d, "sbx", toolbox_url="http://127.0.0.1:9", access_token="t").volumes.pull(
            v,  # type: ignore[arg-type]
            request_id="r",
        ),
    ),
]


_PATH_VALUES: list[tuple[str, object]] = [
    ("None", None),
    ("number", 5),
    ("empty", ""),
    ("blank", " \t\u2028"),
    # Dot segments, which the HTTP client resolves away to another route.
    ("dot", "."),
    ("dot-dot", ".."),
]
# get_limits(organization_id=None) is the key's own organization, not a refusal;
# the names promote() and create_snapshot() send go in a body, not a path, so a
# dot segment is a valid name there.
_BODY_NAMES = ("images.promote name", "create_snapshot")


def _path_case(call_name: str, label: str) -> bool:
    if call_name == "get_limits" and label == "None":
        return False
    return not (call_name in _BODY_NAMES and label in ("dot", "dot-dot"))


_PATH_CASES = [
    (call_name, argument, call, value)
    for call_name, argument, call in _PATH_CALLS
    for label, value in _PATH_VALUES
    if _path_case(call_name, label)
]


@pytest.mark.parametrize(
    ("call_name", "argument", "call", "value"),
    _PATH_CASES,
    ids=[
        f"{case[0]}-{label}"
        for case in _PATH_CALLS
        for label, _ in _PATH_VALUES
        if _path_case(case[0], label)
    ],
)
async def test_a_path_id_or_name_must_be_a_non_blank_string(
    api: FakeApi,
    call_name: str,
    argument: str,
    call: Callable[[AsyncDaytonaV2, object], Awaitable[object]],
    value: object,
) -> None:
    del call_name
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match=f"^{argument} must ") as raised:
            await call(root.v2, value)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert api.requests == []


async def test_set_labels_takes_labels_positionally_and_returns_a_plain_dict() -> None:
    labels = {"team": "ml", "name": "box"}
    with FakeApi([PlannedResponse(200, {"id": "sbx", "labels": labels})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            applied = await sandbox.set_labels(labels)
    assert type(applied) is dict and applied == labels
    assert [json.loads(request.body) for request in fake.requests] == [{"labels": labels}]


_SANDBOX_DETAIL: dict[str, object] = {
    "id": "sbx",
    "class": "linux",
    "name": "sbx",
    "status": "stopped",
    "cpu": 1,
    "cores_max": 1,
    "memory_mib": 1024,
    "disk_mib": 1024,
    "port_map": [],
    "region_id": "region-a",
}


# Shared with the TypeScript SDK's malformed-responses.test.ts: set_labels()
# keeps the reserved `name` label unless the new labels set it, reading the
# sandbox first; with no stored name nothing is added.
@pytest.mark.parametrize(
    ("stored", "given", "sent"),
    [
        ({"name": "box", "team": "a"}, {"team": "b"}, {"team": "b", "name": "box"}),
        ({"team": "a"}, {"team": "b"}, {"team": "b"}),
        ({"name": "box"}, {}, {"name": "box"}),
        (None, {"team": "b"}, {"team": "b"}),
    ],
)
async def test_set_labels_keeps_the_name_label(
    stored: dict[str, str] | None, given: dict[str, str], sent: dict[str, str]
) -> None:
    # stored None: the read reports no labels (none), so no name is sent.
    detail = dict(_SANDBOX_DETAIL) if stored is None else {**_SANDBOX_DETAIL, "labels": stored}
    with FakeApi(
        [
            PlannedResponse(200, detail),
            PlannedResponse(200, {"id": "sbx", "labels": sent}),
        ]
    ) as fake:  # type: ignore[arg-type]
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            applied = await lazy_sandbox(root.v2, "sbx").set_labels(given)
    assert applied == sent
    assert [request.method for request in fake.requests] == ["GET", "PUT"]
    assert json.loads(fake.requests[1].body) == {"labels": sent}


@pytest.mark.parametrize(
    ("argument", "call"),
    [
        ("image_id", lambda d: d.images.get("a\ud800b")),
        ("name", lambda d: d.secrets.set("a\ud800b", "value", allowed_domains=["*"])),
    ],
    ids=["images.get", "secrets.set"],
)
async def test_a_path_id_or_name_with_a_lone_surrogate_is_refused(
    api: FakeApi, argument: str, call: Callable[[AsyncDaytonaV2], Awaitable[object]]
) -> None:
    # It has no UTF-8 form: refused as TypeScript refuses it, never a raw UnicodeEncodeError.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match=f"^{argument} must not contain a surrogate code point$"):
            await call(root.v2)
    assert api.requests == []


@pytest.mark.parametrize(
    ("argument", "kwargs"),
    [
        ("on_logs", {"on_logs": None}),
        ("on_logs", {"on_logs": "x"}),
        ("on_event", {"on_logs": lambda _chunk: None, "on_event": 5}),
    ],
    ids=["on_logs None", "on_logs a string", "on_event a number"],
)
@pytest.mark.parametrize("form", ["images", "sandbox"])
async def test_build_log_callbacks_must_be_callable(
    api: FakeApi, form: str, argument: str, kwargs: dict[str, object]
) -> None:
    # The TypeScript twin covers the same calls.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match=f"^{argument} must be a callable") as raised:
            if form == "images":
                await root.v2.images.follow_build_logs("img", **kwargs)  # type: ignore[arg-type]
            else:
                await lazy_sandbox(root.v2, "sbx").follow_build_logs(**kwargs)  # type: ignore[arg-type]
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert api.requests == []


async def test_the_contract_bounds_are_accepted() -> None:
    # limit 200, idle_expiry_seconds -1, cpu_arch arm64 and if_not_exists=True
    # reach the API as given (the TypeScript twin sends the same).
    secret = {
        "name": "K",
        "allowed_domains": ["*"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    with FakeApi(
        [
            PlannedResponse(200, {"sandboxes": [], "next_cursor": None}),
            PlannedResponse(200, {"id": "img", "status": "pending"}),
            PlannedResponse(500, {"message": "stop"}),
            PlannedResponse(200, secret),
        ]
    ) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.list(limit=200, cursor="c")
            with pytest.raises(DaytonaError) as raised:
                await root.v2.images.build(
                    ImageBuilder.from_registry("alpine"), idle_expiry_seconds=-1, cpu_arch="arm64"
                )
            _assert_server_stop(raised.value)
            await root.v2.secrets.set("K", "v", allowed_domains=["*"], if_not_exists=True)
    assert len(fake.requests) == 4
    assert fake.requests[0].path == "/sandboxes?cursor=c&limit=200"
    assert json.loads(fake.requests[1].body)["idle_expiry_seconds"] == -1
    assert json.loads(fake.requests[1].body)["cpu_arch"] == "arm64"
    assert fake.requests[3].headers.get("If-None-Match") == "*"


async def test_a_path_id_with_an_astral_character_is_percent_encoded_as_utf8() -> None:
    # In Python an astral character is one code point (not a surrogate pair),
    # so it passes and is encoded as its four UTF-8 bytes.
    secret = {
        "name": "a\U0001f600",
        "allowed_domains": ["*"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    with FakeApi([PlannedResponse(200, secret)]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.secrets.get("a\U0001f600")
    assert fake.requests[0].path == "/secrets/a%F0%9F%98%80"


@pytest.mark.parametrize(
    "call",
    [
        lambda d: d.volumes.create(name="\ufeff"),
        lambda d: d.create(volumes=[VolumeMount(volume_id="v", mount_path="\ufeff")]),
    ],
    ids=["volumes.create name U+FEFF", "create volumes[].mount_path U+FEFF"],
)
async def test_u_feff_is_not_python_whitespace_and_is_sent(
    call: Callable[[AsyncDaytonaV2], Awaitable[object]],
) -> None:
    # Python's whitespace set (U+FEFF is not in it, U+001C is); TypeScript tests the same set.
    with FakeApi([PlannedResponse(500, {"message": "stop"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(DaytonaError) as raised:
                await call(root.v2)
    _assert_server_stop(raised.value)
    assert len(fake.requests) == 1


async def test_valid_search_classes_and_timestamps_are_sent() -> None:
    with FakeApi(
        [PlannedResponse(200, {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": None})]
    ) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.search(
                sandbox_class=("linux", "macos"), created_start_at="2026-01-01T00:00:00.5+02:00"
            )
    query = parse_qs(urlsplit(fake.requests[0].path).query)
    assert (query["class"], query["created_start_at"]) == (["linux,macos"], ["2026-01-01T00:00:00.5+02:00"])


async def test_a_search_label_with_a_lone_surrogate_is_sent_json_escaped() -> None:
    # Labels are sent as JSON, which escapes a lone surrogate (as a body does).
    with FakeApi(
        [PlannedResponse(200, {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": None})]
    ) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.search(labels={"team": "a\ud800b"})
    assert "%5Cud800" in fake.requests[0].path


_TIMESTAMPS = json.loads(
    (Path(__file__).parent / "fixtures" / "search-timestamps.json").read_text(encoding="utf-8")
)
_EMPTY_SEARCH: JsonObject = {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": None}


@pytest.mark.parametrize("bound", _TIMESTAMPS["refused"])
async def test_a_search_timestamp_that_is_not_a_real_rfc3339_date_time_is_refused(
    api: FakeApi, bound: str
) -> None:
    # The shared table, as TypeScript reads it.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="RFC 3339"):
            await root.v2.search(created_start_at=bound)
    assert api.requests == []


@pytest.mark.parametrize("bound", _TIMESTAMPS["sent"])
async def test_a_real_rfc3339_search_timestamp_is_sent(bound: str) -> None:
    with FakeApi([PlannedResponse(200, _EMPTY_SEARCH)]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.search(created_start_at=bound)
    assert parse_qs(urlsplit(fake.requests[0].path).query)["created_start_at"] == [bound]


@pytest.mark.parametrize(("count", "accepted"), [(2048, True), (2049, False)])
async def test_a_build_log_cursor_is_bounded_in_code_points(count: int, accepted: bool) -> None:
    # 2048 astral characters (4096 UTF-16 units) pass in both SDKs; 2049 do not.
    cursor = "\U0001f600" * count
    with FakeApi([PlannedResponse(404, {"code": "NOT_FOUND", "message": "stop here"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(DaytonaError) as raised:
                await root.v2.images.follow_build_logs("img", on_logs=print, cursor=cursor)
    assert isinstance(raised.value, BadRequestError) is not accepted
    assert len(fake.requests) == (1 if accepted else 0)


@pytest.mark.parametrize(
    "call",
    [
        lambda d: d.get_limits(organization_id="a\ud800b"),
        lambda d: d.get("a\ud800b"),
        lambda d: d.stop("a\ud800b"),
    ],
    ids=["get_limits", "get", "stop"],
)
async def test_an_id_with_a_lone_surrogate_is_refused_on_the_client_paths(
    api: FakeApi, call: Callable[[AsyncDaytonaV2], Awaitable[object]]
) -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="must not contain a surrogate code point"):
            await call(root.v2)
    assert api.requests == []


@pytest.mark.parametrize(
    ("argument", "call"),
    [
        ("name", lambda d: d.secrets.delete("..")),
        ("name_or_id", lambda d: d.volumes.get(".")),
        ("sandbox", lambda d: d.stop("..")),
        ("sandbox", lambda d: d.delete(".")),
        ("sandbox_id", lambda d: d.get("..")),
    ],
    ids=["secrets.delete ..", "volumes.get .", "stop ..", "delete .", "get .."],
)
async def test_a_dot_segment_id_is_refused_before_any_request(
    api: FakeApi, argument: str, call: Callable[[AsyncDaytonaV2], Awaitable[object]]
) -> None:
    # `secrets.delete("..")` would otherwise go to another route: the HTTP
    # client resolves the dot segment away.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError) as raised:
            await call(root.v2)
    assert str(raised.value) == f"{argument} must not be '.' or '..'"
    assert api.requests == []


@pytest.mark.parametrize("arch", ["x86_64", 5, "", " "])
@pytest.mark.parametrize("route", ["create", "create_from_build"])
async def test_a_create_cpu_arch_outside_the_platform_architectures_is_refused(
    api: FakeApi, route: str, arch: object
) -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="cpu_arch must be"):
            if route == "create":
                await root.v2.create(cpu_arch=arch)  # type: ignore[arg-type]
            else:
                await root.v2.create_from_build(ImageBuilder.from_registry("alpine"), cpu_arch=arch)  # type: ignore[arg-type]
    assert api.requests == []


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
async def test_a_create_cpu_arch_of_the_platform_is_sent(arch: str) -> None:
    with FakeApi([PlannedResponse(200, {"id": "sbx", "status": "running"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.create("img_1", cpu_arch=arch, wait_seconds=0)
    assert json.loads(fake.requests[0].body)["cpu_arch"] == arch


# One header rule in both SDKs (the TypeScript twin pins the same rows): a
# padded value is refused, never stripped; a blank idempotency key or ETag is
# refused; a blank region selects the default region, as create does.
_PADDED_HEADERS: list[tuple[str, str, Callable[[AsyncDaytonaV2, str], Awaitable[object]]]] = [
    ("create region", "region", lambda d, v: d.create(region=v)),
    ("find_by_name region", "region", lambda d, v: d.find_by_name("x", region=v)),
    ("create idempotency_key", "idempotency_key", lambda d, v: d.create(idempotency_key=v)),
    (
        "reservations.create idempotency_key",
        "idempotency_key",
        lambda d, v: d.reservations.create(region="r", idempotency_key=v),
    ),
    (
        "reset_network_policy if_match",
        "if_match",
        lambda d, v: lazy_sandbox(d, "sbx").reset_network_policy(if_match=v),
    ),
]


@pytest.mark.parametrize("value", [" us", "us ", " "], ids=["leading", "trailing", "blank"])
@pytest.mark.parametrize(
    ("name", "argument", "call"), _PADDED_HEADERS, ids=[case[0] for case in _PADDED_HEADERS]
)
async def test_a_padded_header_value_is_refused_before_any_request(
    api: FakeApi,
    name: str,
    argument: str,
    call: Callable[[AsyncDaytonaV2, str], Awaitable[object]],
    value: str,
) -> None:
    if argument == "region" and value == " ":
        pytest.skip("a blank region selects the default region")
    del name
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match=f"^{argument} must "):
            await call(root.v2, value)
    assert api.requests == []


@pytest.mark.parametrize("call", ["create", "find_by_name"])
@pytest.mark.parametrize("region", ["", "  ", " LOCAL "])
async def test_a_blank_region_selects_the_default_region(call: str, region: str) -> None:
    responses = (
        [PlannedResponse(200, {"id": "sbx", "status": "running"})]
        if call == "create"
        else [
            PlannedResponse(200, {"sandboxes": [], "total": 0, "total_relation": "eq", "next_cursor": None})
        ]
    )
    with FakeApi(responses) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            if call == "create":
                await root.v2.create("img_1", region=region, wait_seconds=0)
            else:
                await root.v2.find_by_name("x", region=region)
    request = fake.requests[0]
    assert "x-daytona-region" not in {key.lower() for key in request.headers}
    if call == "create":
        assert "region_id" not in json.loads(request.body)


@pytest.mark.parametrize("token", [None, 5, "", "  "], ids=["None", "number", "empty", "blank"])
async def test_expire_signed_preview_link_needs_a_token(api: FakeApi, token: object) -> None:
    # The TypeScript twin refuses the same values.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="^token must be a non-empty string$"):
            await lazy_sandbox(root.v2, "sbx").ports.expire_signed_preview_link(8080, token)  # type: ignore[arg-type]
    assert api.requests == []


@pytest.mark.parametrize("bound", _TIMESTAMPS["refusedAsDates"])
async def test_a_datetime_bound_outside_years_1_to_9999_in_utc_is_refused(api: FakeApi, bound: str) -> None:
    # Sent in UTC, the instant falls outside years 0001-9999: the shared
    # table, as TypeScript reads it with new Date().
    value = datetime.fromisoformat(bound)
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="without a leap second") as raised:
            await root.v2.search(created_start_at=value)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert isinstance(raised.value.__cause__, OverflowError)
    assert api.requests == []


class _NoOffset(tzinfo):
    """A tzinfo whose utcoffset() is None: naive by Python's own definition."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


class _BadOffset(tzinfo):
    def __init__(self, offset: object) -> None:
        self._offset = offset

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return self._offset  # type: ignore[return-value]

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


async def test_a_datetime_without_an_offset_is_taken_as_utc() -> None:
    with FakeApi([PlannedResponse(200, _EMPTY_SEARCH)]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            await root.v2.search(created_start_at=datetime(2026, 1, 1, 12, tzinfo=_NoOffset()))
    assert parse_qs(urlsplit(fake.requests[0].path).query)["created_start_at"] == ["2026-01-01T12:00:00Z"]


@pytest.mark.parametrize("offset", [timedelta(hours=25), "5"], ids=["out of range", "not a timedelta"])
async def test_a_datetime_whose_offset_fails_is_refused(api: FakeApi, offset: object) -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="without a leap second") as raised:
            await root.v2.search(created_start_at=datetime(2026, 1, 1, tzinfo=_BadOffset(offset)))
    assert isinstance(raised.value.__cause__, (ValueError, TypeError))
    assert api.requests == []


@pytest.mark.parametrize("region", ["", "  ", "local", " LOCAL "])
@pytest.mark.parametrize("route", ["build", "pull"])
async def test_an_image_call_with_a_default_region_sends_no_region_id(route: str, region: str) -> None:
    with FakeApi([PlannedResponse(500, {"message": "stop"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(DaytonaError) as raised:
                if route == "build":
                    await root.v2.images.build(ImageBuilder.from_registry("alpine"), region=region)
                else:
                    await root.v2.images.pull("alpine", region=region, wait=False)
    # The request went out and the fake's own 500 ended the call (not a refusal).
    _assert_server_stop(raised.value)
    assert len(fake.requests) == 1
    assert "region_id" not in json.loads(fake.requests[0].body)


@pytest.mark.parametrize("region", [" us", "us ", 5], ids=["leading", "trailing", "number"])
@pytest.mark.parametrize("route", ["build", "pull"])
async def test_an_image_call_refuses_a_padded_or_mistyped_region(
    api: FakeApi, route: str, region: object
) -> None:
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="^region must "):
            if route == "build":
                await root.v2.images.build(ImageBuilder.from_registry("alpine"), region=region)  # type: ignore[arg-type]
            else:
                await root.v2.images.pull("alpine", region=region, wait=False)  # type: ignore[arg-type]
    assert api.requests == []


@pytest.mark.parametrize("route", ["build", "pull"])
async def test_an_image_call_sends_a_named_region(route: str) -> None:
    with FakeApi([PlannedResponse(500, {"message": "stop"})]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(DaytonaError) as raised:
                if route == "build":
                    await root.v2.images.build(ImageBuilder.from_registry("alpine"), region="us")
                else:
                    await root.v2.images.pull("alpine", region="us", wait=False)
    _assert_server_stop(raised.value)
    assert len(fake.requests) == 1
    assert json.loads(fake.requests[0].body)["region_id"] == "us"


# The two SDKs check a call's arguments in the same order, so the first bad
# one is named alike (the TypeScript twin pins the same rows).
_ORDER: list[tuple[str, Callable[[AsyncDaytonaV2], Awaitable[object]], str]] = [
    ("volumes.get", lambda d: d.volumes.get("", create="x"), "name_or_id"),  # type: ignore[arg-type]
    ("volumes.delete", lambda d: d.volumes.delete("", id_only="x"), "name_or_id"),  # type: ignore[arg-type]
    ("reservations.renew", lambda d: d.reservations.renew("", idempotency_key=" k"), "reservation_id"),
    ("reservations.update", lambda d: d.reservations.update("", auto_renew="x"), "reservation_id"),  # type: ignore[arg-type]
    ("secrets.set", lambda d: d.secrets.set("K", 5, allowed_domains=5), "value"),  # type: ignore[arg-type]
]


@pytest.mark.parametrize(("name", "call", "argument"), _ORDER, ids=[case[0] for case in _ORDER])
async def test_the_first_bad_argument_is_named_as_typescript_names_it(
    api: FakeApi, name: str, call: Callable[[AsyncDaytonaV2], Awaitable[object]], argument: str
) -> None:
    del name
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match=f"^{argument} must "):
            await call(root.v2)
    assert api.requests == []


class _RaisingOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise RuntimeError("offset unavailable")

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


class _JunkDatetime(datetime):
    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        return "junk"


@pytest.mark.parametrize(
    ("value", "cause"),
    [
        (datetime(2026, 1, 1, tzinfo=tzinfo()), NotImplementedError),  # type: ignore[abstract]
        (datetime(2026, 1, 1, tzinfo=_RaisingOffset()), RuntimeError),
        (_JunkDatetime(2026, 1, 1, tzinfo=timezone.utc), None),
    ],
    ids=["abstract tzinfo", "tzinfo raising", "datetime rendering junk"],
)
async def test_a_datetime_bound_whose_conversion_or_rendering_fails_is_refused(
    api: FakeApi, value: datetime, cause: type[BaseException] | None
) -> None:
    # As TypeScript's Date branch: the rendered form is checked like a string.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="without a leap second") as raised:
            await root.v2.search(created_start_at=value)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    if cause is not None:
        assert isinstance(raised.value.__cause__, cause)
    assert api.requests == []


@pytest.mark.parametrize("name", ["", " ", "\t\u2028"])
@pytest.mark.parametrize("call", ["build", "pull", "capture"])
async def test_a_blank_image_name_is_refused_before_any_request(api: FakeApi, call: str, name: str) -> None:
    # As promote() refuses one; the TypeScript twin is in argument-checks.test.ts.
    async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
        with pytest.raises(BadRequestError, match="^name must be a non-empty string$"):
            if call == "build":
                await root.v2.images.build(ImageBuilder.from_registry("alpine"), name=name)
            elif call == "pull":
                await root.v2.images.pull("alpine", name=name, wait=False)
            else:
                await lazy_sandbox(root.v2, "sbx").capture_image(name=name)
    assert api.requests == []


@pytest.mark.parametrize("port", [2280, 2281, 22220])
async def test_a_reserved_port_can_still_be_unexposed_and_its_signed_links_revoked(port: int) -> None:
    # The API refuses the daemon's ports only where a preview is made; the
    # TypeScript twin sends the same requests.
    with FakeApi([PlannedResponse(204, None), PlannedResponse(204, None)]) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            await sandbox.ports.unexpose(port)
            await sandbox.ports.expire_signed_preview_link(port, "tok")
    assert [(request.method, request.path) for request in fake.requests] == [
        ("DELETE", f"/sandboxes/sbx/ports/{port}"),
        ("POST", f"/sandboxes/sbx/ports/{port}/signed-preview-urls/revoke"),
    ]
