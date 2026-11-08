# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The argument rule for the client, lifecycle and platform-service area.

A wrong type or value is a :class:`BadRequestError` (400, ``BAD_REQUEST``,
``SDK``) before any request. The TypeScript twin is
``tests/argument-checks.test.ts``.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timezone

import anyio
import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError, ImageBuilder, PortMapping, RequestTimeoutError, Resources
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
