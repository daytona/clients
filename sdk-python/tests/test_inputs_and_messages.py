# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Transfer, screenshot, create and image-builder inputs are refused before
any request, and the messages of the shared checks match the TypeScript and
Go SDKs. The TypeScript twin is ``tests/inputs-and-messages.test.ts``."""

from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Awaitable, Callable

import pytest
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona as RootAsyncDaytona
from daytona import DaytonaConfig as RootDaytonaConfig
from daytona.v2 import AuthenticationError, BadRequestError, PayloadTooLargeError
from daytona.v2._core import RequestSpec, _wire_statuses
from daytona.v2.computer_use_core import (
    expect_computer_use_point,
    expect_computer_use_process_logs_response,
)
from daytona.v2.computer_use_services import AsyncScreenService
from daytona.v2.computer_use_types import ComputerUseRegion
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.filesystem import AsyncFileSystemService
from daytona.v2.image import ImageBuilder
from daytona.v2.services import AsyncReservationsService
from daytona.v2.types import DaytonaConfig, PortMapping

pytestmark = pytest.mark.anyio


class _Recorder:
    """A transport that records every call and answers none of them."""

    request_timeout_seconds = 30.0
    _source = "sdk-python-async"

    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.specs: list[RequestSpec] = []
        self.calls: list[str] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        if not self.responses:
            raise AssertionError(f"unexpected request {spec.method} {spec.path}")
        return self.responses.pop(0)

    async def request(self, method: str, path: str, **_kwargs: object) -> object:
        self.calls.append(f"{method} {path}")
        raise AssertionError("no request expected")

    async def open_stream(self, spec: RequestSpec) -> object:
        self.calls.append(f"{spec.method} {spec.path}")
        raise AssertionError("no request expected")

    async def close(self) -> None:
        pass


async def _refused(call: Callable[[], Awaitable[object]], message: str) -> None:
    with pytest.raises(BadRequestError) as caught:
        await call()
    assert str(caught.value) == message
    assert (caught.value.status_code, caught.value.code, caught.value.source) == (400, "BAD_REQUEST", "SDK")


def _client(api: _Recorder) -> AsyncDaytona:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = api  # type: ignore[assignment]
    return client


# --- #575: transfer inputs ----------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"cancel": object()},
            "cancel must have an is_set() method, such as a threading.Event or an asyncio.Event",
        ),
        (
            {"cancel": {"is_set": True}},
            "cancel must have an is_set() method, such as a threading.Event or an asyncio.Event",
        ),
        ({"on_progress": 5}, "on_progress must be callable"),
    ],
)
async def test_transfer_options_are_refused_before_any_io(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"x")
    transport = _Recorder()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    await _refused(lambda: fs.upload_file(source, "/tmp/dest", **kwargs), message)
    await _refused(lambda: fs.download_file("/tmp/src", tmp_path / "dest", **kwargs), message)
    assert transport.calls == []
    assert not (tmp_path / "dest").exists()


async def test_atomic_must_be_a_boolean(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"x")
    transport = _Recorder()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    await _refused(lambda: fs.upload_file(source, "/tmp/dest", atomic="yes"), "atomic must be a boolean")  # type: ignore[arg-type]
    await _refused(lambda: fs.write_bytes("/tmp/dest", b"x", atomic=1), "atomic must be a boolean")  # type: ignore[arg-type]
    await _refused(lambda: fs.write_text("/tmp/dest", "x", atomic=None), "atomic must be a boolean")  # type: ignore[arg-type]
    await _refused(lambda: fs.write_many({"/tmp/dest": b"x"}, atomic="no"), "atomic must be a boolean")  # type: ignore[arg-type]
    assert transport.specs == [] and transport.calls == []


async def test_a_threading_event_and_a_custom_is_set_object_are_accepted(tmp_path: Path) -> None:
    class Flag:
        def is_set(self) -> bool:
            return False

    source = tmp_path / "source.txt"
    source.write_bytes(b"x")
    fs = AsyncFileSystemService(_Recorder())  # type: ignore[arg-type]
    for cancel in (threading.Event(), Flag()):
        # The checks pass and the call reaches the transport.
        with pytest.raises(AssertionError, match="no request expected"):
            await fs.upload_file(source, "/tmp/dest", cancel=cancel, on_progress=lambda _sent, _total: None)


async def test_uploading_a_local_directory_is_refused_before_any_request(tmp_path: Path) -> None:
    transport = _Recorder()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    await _refused(lambda: fs.upload_file(tmp_path, "/tmp/dest"), "local_path must be a regular file")
    assert transport.calls == []


# --- #575: screenshot region ---------------------------------------------------


@pytest.mark.parametrize(
    ("region", "message"),
    [
        ({"x": 0, "y": 0, "width": 1, "height": 1}, "region must be a ComputerUseRegion"),
        (ComputerUseRegion(x=1.5, y=0, width=1, height=1), "region.x must be an integer"),  # type: ignore[arg-type]
        (ComputerUseRegion(x=0, y=True, width=1, height=1), "region.y must be an integer"),
        (ComputerUseRegion(x=0, y=0, width="1", height=1), "region.width must be an integer"),  # type: ignore[arg-type]
        (ComputerUseRegion(x=0, y=0, width=0, height=1), "region.width must be greater than 0"),
        (ComputerUseRegion(x=0, y=0, width=1, height=-2), "region.height must be greater than 0"),
        (
            ComputerUseRegion(x=2**53, y=0, width=1, height=1),
            "region.x must be between -9007199254740991 and 9007199254740991",
        ),
    ],
)
async def test_screenshot_region_is_checked_before_any_request(region: object, message: str) -> None:
    transport = _Recorder()
    screen = AsyncScreenService(transport)  # type: ignore[arg-type]
    await _refused(lambda: screen.screenshot(region=region), message)  # type: ignore[arg-type]
    assert transport.specs == []


async def test_a_valid_screenshot_region_is_sent_as_integers() -> None:
    transport = _Recorder([{"screenshot": "aGk=", "sizeBytes": 2}])
    screen = AsyncScreenService(transport)  # type: ignore[arg-type]
    await screen.screenshot(region=ComputerUseRegion(x=-5, y=0, width=10, height=20))
    assert transport.specs[0].path == "/computeruse/screenshot/region"
    assert transport.specs[0].params == {"x": -5, "y": 0, "width": 10, "height": 20}


# --- #575: create env and idempotency key ---------------------------------------


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"A\ud800": "x"}, "env keys must not contain a lone surrogate"),
        ({"A": "x\udc00"}, "env values must not contain a lone surrogate"),
        ({"A=B": "x"}, "env key is not a valid variable name"),
        ({"": "x"}, "env key is not a valid variable name"),
        ({"A\nB": "x"}, "env key is not a valid variable name"),
        ({"A": "x\0y"}, "env values must not contain NUL"),
    ],
)
async def test_create_env_follows_the_update_env_rule(env: dict[str, str], message: str) -> None:
    api = _Recorder()
    client = _client(api)
    await _refused(lambda: client.create("img", env=env), message)
    await _refused(lambda: client.create_macos("base-macos", env=env), message)
    await _refused(
        lambda: client.create_from_build(ImageBuilder.from_registry("python:3.12"), env=env), message
    )
    assert api.specs == []


async def test_an_idempotency_key_over_256_bytes_is_refused_before_any_request() -> None:
    api = _Recorder()
    client = _client(api)
    reservations = AsyncReservationsService(api)  # type: ignore[arg-type]
    message = "idempotency_key must be at most 256 bytes"
    await _refused(lambda: client.create("img", idempotency_key="x" * 257), message)
    await _refused(
        lambda: client.create_from_build(
            ImageBuilder.from_registry("python:3.12"), idempotency_key="x" * 257
        ),
        message,
    )
    await _refused(lambda: reservations.create(region="r", idempotency_key="x" * 257), message)
    await _refused(lambda: reservations.renew("res", idempotency_key="x" * 257), message)
    assert api.specs == []


def _created(sandbox_id: str = "sbx") -> dict[str, object]:
    return {"id": sandbox_id, "class": "linux", "status": "running", "region_id": "region-a"}


async def test_create_sends_the_idempotency_key_as_the_header_only() -> None:
    api = _Recorder([_created(), _created(), _created()])
    client = _client(api)
    await client.create("img", idempotency_key="k" * 256, wait_seconds=0)
    await client.create("img", wait_seconds=0)
    await client.create("img", name="box", wait_seconds=0)
    explicit, random_key, named = api.specs
    for spec in api.specs:
        assert isinstance(spec.json_body, dict) and "idempotency_key" not in spec.json_body
    assert explicit.headers["Idempotency-Key"] == "k" * 256
    generated = random_key.headers["Idempotency-Key"]
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", generated)
    assert uuid.UUID(generated).version == 4
    assert named.headers["Idempotency-Key"] == "name:box"


# --- #575: registry references ---------------------------------------------------


@pytest.mark.parametrize("ref", ["python 3", "python:3.12 AS base", "py\u00a0thon", "py\u2003thon"])
def test_a_registry_reference_with_whitespace_is_refused(ref: str) -> None:
    with pytest.raises(BadRequestError, match="must not contain whitespace") as caught:
        ImageBuilder.from_registry(ref)
    assert caught.value.source == "SDK"


@pytest.mark.parametrize(
    "ref",
    [
        "python:3.12-slim",
        "  python:3.12  ",
        "registry.example.com:5000/team/app:v1",
        "ghcr.io/org/app@sha256:" + "a" * 64,
        "localhost:5000/app:latest@sha256:" + "b" * 64,
    ],
)
def test_valid_registry_references_are_kept(ref: str) -> None:
    assert ImageBuilder.from_registry(ref).to_dockerfile() == f"FROM {ref.strip()}\n"


# --- #571: shared messages ------------------------------------------------------


async def test_missing_and_conflicting_credentials_name_the_config_and_the_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DAYTONA_API_KEY", "DAYTONA_JWT_TOKEN", "DAYTONA_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AuthenticationError) as missing:
        AsyncDaytona(DaytonaConfig())
    assert str(missing.value) == (
        "configure api_key or jwt_token (in DaytonaConfig or as DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)"
    )
    monkeypatch.setenv("DAYTONA_API_KEY", "key")
    monkeypatch.setenv("DAYTONA_JWT_TOKEN", "jwt")
    with pytest.raises(BadRequestError) as both:
        AsyncDaytona(DaytonaConfig())
    assert str(both.value) == (
        "configure exactly one of api_key or jwt_token (in DaytonaConfig or as DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)"
    )
    with pytest.raises(BadRequestError) as no_project:
        AsyncDaytona(DaytonaConfig(jwt_token="jwt"))
    assert (
        str(no_project.value) == "jwt_token requires project_id (in DaytonaConfig or as DAYTONA_PROJECT_ID)"
    )


@pytest.mark.parametrize(
    ("states", "message"),
    [
        (5, "states must be a sandbox state or a list of sandbox states"),
        ([], "states must name at least one sandbox state"),
        ("unknown", 'states entries must be known sandbox states, got "unknown"'),
        (["started", 3], "states entries must be strings"),
        ([True], "states entries must be strings"),
        ([None], "states entries must be strings"),
        ("météo", 'states entries must be known sandbox states, got "météo"'),
    ],
)
def test_search_states_messages(states: object, message: str) -> None:
    with pytest.raises(BadRequestError) as caught:
        _wire_statuses(states)
    assert str(caught.value) == message


async def test_wait_timeouts_share_one_message() -> None:
    fs = AsyncFileSystemService(_Recorder())  # type: ignore[arg-type]
    await _refused(
        lambda: fs.move("/a", "/b", timeout=-1),
        "timeout must be a non-negative finite number of seconds",
    )
    with pytest.raises(BadRequestError) as caught:
        AsyncDaytona(DaytonaConfig(api_key="key", request_timeout=-1))
    assert str(caught.value) == "request_timeout must be a non-negative finite number of seconds"


@pytest.mark.parametrize(
    ("parse", "payload", "message"),
    [
        (
            expect_computer_use_point,
            {"x": "a", "y": 1},
            "expected computer-use point response: x must be an integer",
        ),
        (expect_computer_use_point, {"x": 1}, "expected computer-use point response: y is required"),
        (
            expect_computer_use_process_logs_response,
            {"processName": "xvfb"},
            "expected computer-use process logs response: logs is required",
        ),
        (
            expect_computer_use_process_logs_response,
            {"processName": "xvfb", "logs": 5},
            "expected computer-use process logs response: logs must be a string",
        ),
    ],
)
def test_malformed_response_messages_use_sdk_wording(
    parse: Callable[[object], object], payload: object, message: str
) -> None:
    with pytest.raises(BadRequestError) as caught:
        parse(payload)
    assert str(caught.value) == message
    assert "Input should" not in str(caught.value) and "Field required" not in str(caught.value)


async def test_an_invalid_port_protocol_is_reported_as_such() -> None:
    api = _Recorder()
    client = _client(api)
    await _refused(
        lambda: client.create("img", ports=[PortMapping(port=80, proto="sctp")]),  # type: ignore[arg-type]
        "ports[].proto must be 'tcp' or 'udp'",
    )
    await _refused(
        lambda: client.create("img", ports=[PortMapping(port=0)]),
        "ports[].port must be an integer in 1..65535",
    )
    await _refused(
        lambda: client.create("img", ports=[{"port": 80}]),  # type: ignore[list-item]
        "ports must be a list of PortMapping(port=...)",
    )
    assert api.specs == []


def _detail(url: str) -> dict[str, object]:
    return {
        "id": "sbx",
        "class": "linux",
        "name": "sbx",
        "status": "running",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 1024,
        "port_map": [],
        "region_id": "region-a",
        "toolbox_proxy_url": f"{url}/toolbox",
    }


async def test_the_payload_too_large_hint_depends_on_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = 2048
    with FakeApi([]) as fake:
        fake._responses.extend(
            [
                PlannedResponse(200, _detail(fake.url)),
                PlannedResponse(200, {"items": [{"name": "x" * (cap * 2)}]}),
                PlannedResponse(200, b"y" * (cap * 2)),
            ]
        )
        monkeypatch.setenv("DAYTONA_V2_API_URL", fake.url)
        async with RootAsyncDaytona(RootDaytonaConfig(api_key="key", max_response_bytes=cap)) as root:
            sandbox = await root.v2.get("sbx", access_token="tok")
            with pytest.raises(PayloadTooLargeError) as listed:
                await sandbox.fs.list("/etc")
            with pytest.raises(PayloadTooLargeError) as read:
                await sandbox.fs.read_bytes("/big")
    assert str(listed.value) == (
        f"the response body is larger than the {cap}-byte max_response_bytes limit; "
        "raise the client's max_response_bytes (0 = no limit)"
    )
    assert str(read.value) == (
        f"the response body is larger than the {cap}-byte max_response_bytes limit; "
        "pass a higher max_response_bytes for this call (0 = no limit), or use download_file() for large files"
    )
    for error in (listed.value, read.value):
        assert (error.status_code, error.code, error.source) == (413, "PAYLOAD_TOO_LARGE", "SDK")
