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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    ("create create_timeout_seconds above 2**53-1", lambda d, _url: d.create(create_timeout_seconds=2**53)),
    ("create create_timeout_seconds 10**400", lambda d, _url: d.create(create_timeout_seconds=10**400)),
    ("create memory above int32 MiB", lambda d, _url: d.create(resources=Resources(memory=2_097_152))),
    ("create disk_max above int32 MiB", lambda d, _url: d.create(resources=Resources(disk_max=2_097_152))),
    ("create cpu 10**400", lambda d, _url: d.create(resources=Resources(cpu=10**400))),
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
# JavaScript's trim(); U+FEFF is the reverse. TypeScript follows this set.
@pytest.mark.parametrize(("name", "stamped"), [("\x1c\x85", False), ("\ufeff", True)])
async def test_a_blank_name_follows_python_whitespace(name: str, stamped: bool) -> None:
    body, key = await _create_body(name)
    labels = body.get("labels")
    assert (isinstance(labels, dict) and "name" in labels) is stamped
    assert key.startswith("name") is stamped


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
    "format_version": 1,
    "format_outdated": False,
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
    "sandboxes_per_mac": 2,
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
]
# get_limits(organization_id=None) is the key's own organization, not a refusal.
_PATH_CASES = [
    (call_name, argument, call, value)
    for call_name, argument, call in _PATH_CALLS
    for label, value in _PATH_VALUES
    if not (call_name == "get_limits" and label == "None")
]


@pytest.mark.parametrize(
    ("call_name", "argument", "call", "value"),
    _PATH_CASES,
    ids=[
        f"{case[0]}-{label}"
        for case in _PATH_CALLS
        for label, _ in _PATH_VALUES
        if not (case[0] == "get_limits" and label == "None")
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
    with FakeApi([PlannedResponse(200, {"id": "sbx", "labels": {"team": "ml"}})] * 2) as fake:
        os.environ["DAYTONA_V2_API_URL"] = fake.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "sbx")
            applied = await sandbox.set_labels({"team": "ml"})
            by_keyword = await sandbox.set_labels(labels={"team": "ml"})
    assert type(applied) is dict and applied == {"team": "ml"} == by_keyword
    assert [json.loads(request.body) for request in fake.requests] == [{"labels": {"team": "ml"}}] * 2


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
        with pytest.raises(BadRequestError, match=f"^{argument} must not contain a lone surrogate$"):
            await call(root.v2)
    assert api.requests == []
