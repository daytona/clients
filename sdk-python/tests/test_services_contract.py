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
from types import MappingProxyType

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
    SearchResult,
)
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.services import (
    AsyncImagesService,
    AsyncMacModelsService,
    AsyncSecretsService,
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
        request_timeout_seconds = 0.0  # no request timeout: only the budget and the 60 s cap bound a hold

        async def send(self, spec: RequestSpec) -> object:
            holds.append(spec.params.get("timeout_ms"))
            now[0] += int(spec.params["timeout_ms"]) / 1000  # type: ignore[call-overload]
            return await super().send(spec)

    api = _Holding([_image("img", "building"), _image("img", "building"), _image("img", "building")])
    image = await AsyncImagesService(api).get("img", wait="ready", timeout=150)  # type: ignore[arg-type]

    assert holds == [60_000, 60_000, 30_000]
    # The budget is spent: the image comes back as last observed, no error.
    assert image.status == "building"

    # A request timeout shorter than 60 s bounds each hold too.
    holds.clear()
    now[0] = 0.0
    _Holding.request_timeout_seconds = 20.0
    api = _Holding([_image("img", "building"), _image("img", "ready")])
    image = await AsyncImagesService(api).get("img", wait="ready", timeout=150)  # type: ignore[arg-type]
    assert (holds, image.status) == ([20_000, 20_000], "ready")


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
    page: ListResult[int] = ListResult([1, 2], "next")
    found: SearchResult[int] = SearchResult([3], None, 1, "eq", "eq")
    assert (page.items, found.items) == ((1, 2), (3,))
    assert not hasattr(page, "total")
    with pytest.raises(dataclasses.FrozenInstanceError):
        page.items = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        page.items.append(3)  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_volume_options_are_read_only() -> None:
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
    assert isinstance(volume.options, MappingProxyType)
    assert volume.options == {"bucket": "b"}
    with pytest.raises(TypeError):
        volume.options["bucket"] = "other"  # type: ignore[index]


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
    for call in (
        sandbox.update_network_policy(network_policy={"block_all": True}, if_match=if_match),  # type: ignore[arg-type]
        sandbox.reset_network_policy(if_match=if_match),  # type: ignore[arg-type]
        sandbox.replace_secrets(secrets=[], if_match=if_match),  # type: ignore[arg-type]
    ):
        with pytest.raises(BadRequestError, match="if_match must be a non-empty string"):
            await call


@pytest.mark.anyio
async def test_platform_inputs_are_validated_before_any_request() -> None:
    api = _Api([])
    secrets = AsyncSecretsService(api)  # type: ignore[arg-type]
    volumes = AsyncVolumesService(api)  # type: ignore[arg-type]
    for domains in ("example.com", ["example.com", 1], None, 5):
        with pytest.raises(BadRequestError, match="allowed_domains must be a list of strings"):
            await secrets.set("S", "v", allowed_domains=domains)  # type: ignore[arg-type]
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
