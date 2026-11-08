# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Platform services, list envelopes and input validation shared with the TypeScript SDK.

The TypeScript SDK holds the same rules in tests/artifact-waits.test.ts,
tests/volumes-service.test.ts, tests/secrets-service.test.ts,
tests/client-lifecycle-options.test.ts and tests/create-wait-failures.test.ts.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest
from _handles import lazy_sandbox

from daytona import v2
from daytona.v2 import (
    BadRequestError,
    ClientTimeoutError,
    ImageBuilder,
    ImageBuildFailedError,
    ListResult,
    Resources,
    SearchResult,
)
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.services import (
    AsyncImagesService,
    AsyncMacModelsService,
    AsyncSecretsService,
    AsyncSnapshotsService,
    AsyncVolumesService,
)
from daytona.v2.types import DaytonaConfig, ImageRef, SnapshotRef

_TIME = "2026-08-24T00:00:00Z"


class _Api:
    request_timeout_seconds = 30.0
    _source = "sdk-python-async"

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _image(image_id: str, status: str, **overrides: object) -> dict[str, object]:
    return {
        "id": image_id,
        "name": None,
        "status": status,
        "region_id": "region-a",
        "anonymous": True,
        "retention": "idle",
        "origin": "dockerfile",
        "created_at": _TIME,
        "updated_at": _TIME,
        "format_version": 2,
        "format_outdated": False,
        "class": "linux",
        "region_ids": ["region-a"],
        **overrides,
    }


def _client(api: _Api) -> AsyncDaytona:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    client._api = api  # type: ignore[assignment]
    client.images._transport = api  # type: ignore[assignment]
    return client


@pytest.mark.anyio
async def test_images_get_repeats_the_long_poll_until_settled_within_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 150 s budget is three long polls (60 + 60 + 30 s), each answered at its end.
    now = [0.0]
    monkeypatch.setattr("daytona.v2._clock.monotonic", lambda: now[0])
    holds: list[object] = []

    class _Holding(_Api):
        request_timeout_seconds = 0.0

        async def send(self, spec: RequestSpec) -> object:
            holds.append(spec.params.get("timeout_ms"))
            now[0] += int(spec.params["timeout_ms"]) / 1000  # type: ignore[call-overload]
            return await super().send(spec)

    api = _Holding([_image("img", "building"), _image("img", "building"), _image("img", "building")])
    image = await AsyncImagesService(api).get("img", wait="ready", timeout=150)  # type: ignore[arg-type]

    assert holds == [60_000, 60_000, 30_000]
    # The budget is spent: the image comes back as last observed, no error.
    assert image.status == "building"

    # A shorter request timeout does not shorten a hold: the wait budget governs,
    # and each hold is whole milliseconds.
    holds.clear()
    now[0] = 0.0
    _Holding.request_timeout_seconds = 20.0
    api = _Holding([_image("img", "building"), _image("img", "ready")])
    image = await AsyncImagesService(api).get("img", wait="ready", timeout=90.25)  # type: ignore[arg-type]
    assert (holds, image.status) == ([60_000, 30_250], "ready")
    assert all(isinstance(hold, int) for hold in holds)


@pytest.mark.anyio
@pytest.mark.parametrize("settled", ["ready", "failed", "error"])
async def test_images_get_stops_polling_once_the_image_settles(settled: str) -> None:
    api = _Api([_image("img", settled)])
    image = await AsyncImagesService(api).get("img", wait="ready", timeout=150)  # type: ignore[arg-type]
    assert image.status == settled
    assert len(api.specs) == 1


@pytest.mark.anyio
async def test_an_image_in_error_is_a_failed_build() -> None:
    api = _Api(
        [{"id": "img", "status": "pending"}, _image("img", "building"), _image("img", "error", error="boom")]
    )
    client = _client(api)
    with pytest.raises(ImageBuildFailedError) as raised:
        await client.create_from_build(ImageBuilder.from_registry("python:3.12").run_commands("true"))
    assert (raised.value.image_id, raised.value.build_error, raised.value.source) == ("img", "boom", "SDK")
    assert [spec.method for spec in api.specs] == ["POST", "GET", "GET"]


@pytest.mark.anyio
@pytest.mark.parametrize("call", ["build", "create_from_build"])
async def test_a_slow_build_submission_ends_at_the_request_timeout(call: str) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"id": "img", "status": "pending"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        transport = AsyncTransport("https://api.test", "key", request_timeout=0.2, client=http)  # type: ignore[arg-type]
        builder = ImageBuilder.from_dockerfile("FROM python:3.12")
        with pytest.raises(ClientTimeoutError):
            if call == "build":
                await AsyncImagesService(transport).build(builder)
            else:
                client = AsyncDaytona(DaytonaConfig(api_key="key"))
                client.images._transport = transport
                try:
                    # The build budget bounds the build wait only, not the submission.
                    await client.create_from_build(builder, build_timeout=600)
                finally:
                    await client.close()


def test_list_and_search_envelopes_are_immutable() -> None:
    page: ListResult[int] = ListResult(items=[1, 2], next_cursor="next")
    found: SearchResult[int] = SearchResult(items=[3], total=1, total_relation="eq", raw_total_relation="eq")
    with pytest.raises(TypeError):
        SearchResult([3], None, 1)  # type: ignore[misc]
    with pytest.raises(TypeError):
        iter(found)  # type: ignore[call-overload]
    assert (page.items, found.items) == ((1, 2), (3,))
    assert not hasattr(page, "total")
    with pytest.raises(dataclasses.FrozenInstanceError):
        page.items = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        page.items.append(3)  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_volume_options_are_a_plain_copy_owned_by_the_volume() -> None:
    api = _Api(
        [
            {
                "id": "vol",
                "name": "data",
                "type": "s3",
                "status": "ready",
                "created_at": _TIME,
                "updated_at": _TIME,
                "options": {"bucket": "b"},
            }
        ]
    )
    volume = await AsyncVolumesService(api).get("vol")  # type: ignore[arg-type]
    # A plain dict (Mapping to type checkers): it serializes like any model field.
    assert type(volume.options) is dict
    assert volume.options == {"bucket": "b"}
    assert json.loads(json.dumps(volume.options)) == {"bucket": "b"}


@pytest.mark.anyio
async def test_get_takes_a_caller_access_token_the_handle_never_replaces() -> None:
    detail = {
        "id": "sbx",
        "class": "linux",
        "status": "running",
        "name": "sbx",
        "cpu": 1,
        "cores_max": 1,
        "memory_mib": 1024,
        "disk_mib": 2048,
        "port_map": [],
        "region_id": "region-a",
        "toolbox_proxy_url": "https://proxy.example",
    }
    client = _client(_Api([detail]))
    sandbox = await client.get("sbx", access_token="caller-token")
    assert sandbox.access_token == "caller-token"
    assert sandbox._access_token_caller_supplied is True
    assert sandbox._sandbox_token_mintable is False
    assert "access_token" not in sandbox.to_json()
    assert not hasattr(sandbox, "toolbox_proxy_url")


@pytest.mark.anyio
@pytest.mark.parametrize("if_match", ["", None])
async def test_if_match_must_be_a_non_empty_string(if_match: object) -> None:
    sandbox = lazy_sandbox(AsyncDaytona(DaytonaConfig(api_key="key")), "sbx")
    # Each operation runs inside its own pytest.raises, so all three are checked.
    with pytest.raises(BadRequestError, match="if_match must be a non-empty string"):
        await sandbox.update_network_policy(network_policy={"block_all": True}, if_match=if_match)  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match="if_match must be a non-empty string"):
        await sandbox.reset_network_policy(if_match=if_match)  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match="if_match must be a non-empty string"):
        await sandbox.replace_secrets(secrets=[], if_match=if_match)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_platform_inputs_are_validated_before_any_request() -> None:
    api = _Api([])
    secrets = AsyncSecretsService(api)  # type: ignore[arg-type]
    volumes = AsyncVolumesService(api)  # type: ignore[arg-type]
    for domains in ("example.com", ["example.com", 1], None, 5):
        with pytest.raises(BadRequestError, match="allowed_domains must be a list of strings"):
            await secrets.set("S", "v", allowed_domains=domains)  # type: ignore[arg-type]
    for blank in ("", "  "):
        with pytest.raises(BadRequestError, match="name must be a non-empty string"):
            await volumes.create(name=blank)
    for size in (0, -1, 1.5, True, float("nan"), float("inf")):
        with pytest.raises(BadRequestError, match="size_gib must be a positive integer"):
            await volumes.create(name="v", size_gib=size)  # type: ignore[arg-type]
    client = _client(api)
    with pytest.raises(BadRequestError, match="create_timeout_seconds must be an integer of at least -1"):
        await client.create("img", create_timeout_seconds=-2)
    with pytest.raises(BadRequestError, match="labels must be a mapping of string to string"):
        await client.create("img", labels={"team": 1})  # type: ignore[dict-item]
    with pytest.raises(BadRequestError, match="labels must be a mapping of string to string"):
        await client.search(labels={"team": None})  # type: ignore[dict-item]
    with pytest.raises(BadRequestError, match="known sandbox states"):
        await client.search(states=["unknown"])  # type: ignore[list-item]
    assert api.specs == []


@pytest.mark.anyio
async def test_mac_models_list_is_a_tuple() -> None:
    api = _Api([{"mac_models": []}])
    assert await AsyncMacModelsService(api).list() == ()  # type: ignore[arg-type]


def test_public_types_are_exported_and_value_types_keyword_only() -> None:
    for name in (
        "SandboxState",
        "SearchSandboxState",
        "JsonValue",
        "CpuArch",
        "ImageStatus",
        "RegistryImageSource",
    ):
        assert name in v2.__all__
    with pytest.raises(TypeError):
        ImageBuilder("python:3.12")  # type: ignore[misc]
    with pytest.raises(TypeError):
        ImageRef("img_1", None)  # type: ignore[misc]
    with pytest.raises(TypeError):
        SnapshotRef("snap_1", None)  # type: ignore[misc]


@pytest.mark.anyio
@pytest.mark.parametrize("call", ["pull", "get"])
async def test_a_long_poll_outlives_a_shorter_request_timeout(call: str) -> None:
    # The long poll answers after 0.5 s; the request timeout is 0.2 s.
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "img", "status": "pending"})
        if "wait" in request.url.params:
            assert request.url.params["timeout_ms"].isdigit()
            await asyncio.sleep(0.5)
            return httpx.Response(200, json=_image("img", "ready"))
        return httpx.Response(200, json=_image("img", "building"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        transport = AsyncTransport("https://api.test", "key", request_timeout=0.2, client=http)  # type: ignore[arg-type]
        images = AsyncImagesService(transport)
        if call == "pull":
            image = await images.pull("python:3.12", timeout=5)
        else:
            image = await images.get("img", wait="ready", timeout=5)
    # The wait budget governs the poll, not the request timeout.
    assert image.status == "ready"


def test_sdk_built_classes_take_only_private_constructor_parameters() -> None:
    # The handle and the services are built by the SDK: their constructor
    # parameters are `_`-prefixed (async classes) or `_impl` / `_bridge` (sync).
    import inspect

    from daytona.v2 import _sync
    from daytona.v2.docker import AsyncDockerService
    from daytona.v2.ports import AsyncPortsService
    from daytona.v2.sandbox import AsyncSandbox
    from daytona.v2.sandbox_volumes import AsyncSandboxVolumesService
    from daytona.v2.services import AsyncReservationsService, AsyncSnapshotsService

    classes: list[type] = [
        AsyncSandbox,
        AsyncImagesService,
        AsyncSnapshotsService,
        AsyncVolumesService,
        AsyncSecretsService,
        AsyncReservationsService,
        AsyncMacModelsService,
        AsyncPortsService,
        AsyncDockerService,
        AsyncSandboxVolumesService,
    ]
    classes += [getattr(_sync, cls.__name__.removeprefix("Async")) for cls in classes]
    for cls in classes:
        names = [name for name in inspect.signature(cls.__init__).parameters if name != "self"]
        assert names and all(name.startswith("_") for name in names), (cls.__name__, names)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("secrets", "s1", "secrets must be a list of secret names"),
        ("secrets", ["s1", 2], "secrets must be a list of secret names"),
        ("ports", [{"port": 80}], "ports must be a list of PortMapping"),
        ("ports", "80", "ports must be a list of PortMapping"),
        ("volumes", [{"volume_id": "v", "mount_path": "/m"}], "volumes must be a list of VolumeMount"),
        ("spot", "yes", "spot must be a boolean"),
        ("user", 5, "user must be a string"),
        ("cpu_arch", 5, "cpu_arch must be a string"),
        ("env", {"A": 1}, "env must be a mapping of string to string"),
        ("labels", {"a": 1}, "labels must be a mapping of string to string"),
        ("resources", Resources(cpu="2"), "resources.cpu must be a positive number of cores"),  # type: ignore[arg-type]
    ],
)
async def test_mistyped_create_options_are_refused_before_any_request(
    option: str, value: object, message: str
) -> None:
    # A mistyped value is refused, never silently dropped (no sandbox without its secret or ports),
    # and create_from_build refuses it before the build, so a caller error costs no build.
    api = _Api([])
    client = _client(api)
    with pytest.raises(BadRequestError, match=message):
        await client.create("img", **{option: value})  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match=message):
        await client.create_from_build(ImageBuilder.from_registry("python:3.12"), **{option: value})  # type: ignore[arg-type]
    assert api.specs == []


@pytest.mark.anyio
async def test_fork_refuses_an_empty_name_and_artifact_gets_refuse_unknown_waits() -> None:
    api = _Api([])
    sandbox = lazy_sandbox(_client(api), "sbx")
    sandbox._api = api  # type: ignore[attr-defined]
    for name in ("", "  "):
        with pytest.raises(BadRequestError, match="fork name must be a non-empty string"):
            await sandbox.fork(name=name)
    images = AsyncImagesService(api)  # type: ignore[arg-type]
    snapshots = AsyncSnapshotsService(api)  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match='wait must be "ready"'):
        await images.get("img", wait="started")  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match='wait must be "ready"'):
        await snapshots.get("snap", wait="started")  # type: ignore[arg-type]
    assert api.specs == []


def test_volume_mount_and_port_mapping_are_keyword_only() -> None:
    from daytona.v2 import PortMapping, VolumeMount

    with pytest.raises(TypeError):
        VolumeMount("vol", "/mnt")  # type: ignore[misc]
    with pytest.raises(TypeError):
        PortMapping(8080)  # type: ignore[misc]


@pytest.mark.anyio
@pytest.mark.parametrize("builder", ["FROM python:3.12", None, {"dockerfile": "FROM x"}])
async def test_images_build_refuses_anything_but_an_image_builder(builder: object) -> None:
    api = _Api([])
    with pytest.raises(BadRequestError, match=r"images\.build\(\) takes an ImageBuilder"):
        await AsyncImagesService(api).build(builder)  # type: ignore[arg-type]
    assert api.specs == []
