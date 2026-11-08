# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The v2 client, sandbox lifecycle and platform services contract.

Fork completion, create waits that never raise, resource units, read-only
handle metadata, the SDK-owned models and the names the 2.x surface keeps.
The TypeScript SDK holds the same rules in tests/sandbox-fork.test.ts,
tests/create-wait-failures.test.ts, tests/sandbox-waits.test.ts,
tests/lifecycle-observed-state.test.ts, tests/client-lifecycle-options.test.ts
and tests/volumes-service.test.ts / tests/secrets-service.test.ts.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime, timezone
from typing import Callable, Optional

import pytest
from _handles import lazy_sandbox

from daytona import v2
from daytona.v2 import (
    BadRequestError,
    ClientTimeoutError,
    ConnectionError,
    ForbiddenError,
    ImageBuilder,
    MacosResources,
    RequestTimeoutError,
    Resources,
    SandboxCreateRequestTimeoutError,
    Secret,
)
from daytona.v2._core import RequestSpec
from daytona.v2._events import WaitOutcome
from daytona.v2._sync import Daytona
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.errors import create_error
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.services import AsyncSecretsService, AsyncVolumesService
from daytona.v2.types import DaytonaConfig


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


def _detail(sandbox_id: str, status: str, **overrides: object) -> dict[str, object]:
    return {
        "class": "linux",
        "cpu": 1,
        "cores_max": 1,
        "id": sandbox_id,
        "memory_mib": 1024,
        "disk_mib": 2048,
        "name": sandbox_id,
        "port_map": [],
        "region_id": "region-a",
        "status": status,
        "toolbox_proxy_url": "https://proxy.example",
        **overrides,
    }


def _fork_response(fork_id: str) -> dict[str, object]:
    return {
        "id": fork_id,
        "status": "pending",
        "forked_from": "src",
        "toolbox_proxy_url": "https://proxy.example",
    }


def _client() -> AsyncDaytona:
    return AsyncDaytona(DaytonaConfig(api_key="key"))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source_status", "memory", "fork_statuses", "expected"),
    [
        # A running source: the fork comes up by itself.
        ("running", None, ["booting", "running"], "started"),
        # A paused source: a memory fork rests paused...
        ("paused", None, ["pending", "paused"], "paused"),
        ("paused", True, ["pending", "paused"], "paused"),
        # ...a disk-only fork rests stopped.
        ("paused", False, ["pending", "stopped"], "stopped"),
        # A stopped source: the fork rests stopped.
        ("stopped", None, ["pending", "stopped"], "stopped"),
    ],
)
async def test_linux_fork_waits_for_the_state_the_fork_settles_in(
    source_status: str, memory: Optional[bool], fork_statuses: list[str], expected: str
) -> None:
    sandbox = lazy_sandbox(_client(), "src")
    api = _Api(
        [
            _detail("src", source_status),
            _fork_response("f"),
            *[_detail("f", status) for status in fork_statuses],
        ]
    )
    sandbox._api = api  # type: ignore[attr-defined]

    fork = await sandbox.fork(memory=memory, wait_seconds=10)

    assert fork.state == expected
    assert [(spec.method, spec.path) for spec in api.specs][:2] == [
        ("GET", "/sandboxes/src"),
        ("POST", "/sandboxes/src/fork"),
    ]
    assert api.responses == []


@pytest.mark.anyio
async def test_fork_with_wait_seconds_zero_returns_once_accepted() -> None:
    sandbox = lazy_sandbox(_client(), "src")
    sandbox._update(sandbox_class="linux")
    api = _Api([_fork_response("f"), _detail("f", "pending")])
    sandbox._api = api  # type: ignore[attr-defined]

    fork = await sandbox.fork(wait_seconds=0)

    assert fork.state == "creating"
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/sandboxes/src/fork"),
        ("GET", "/sandboxes/f"),
    ]


@pytest.mark.anyio
async def test_fork_never_raises_because_of_how_its_wait_ended() -> None:
    sandbox = lazy_sandbox(_client(), "src")
    api = _Api(
        [_detail("src", "running"), _fork_response("f"), _detail("f", "pending"), _detail("f", "failed")]
    )
    sandbox._api = api  # type: ignore[attr-defined]
    failed = await sandbox.fork(wait_seconds=10)
    assert failed.state == "error"

    slow_source = lazy_sandbox(_client(), "src")
    slow_api = _Api(
        [_detail("src", "running"), _fork_response("f"), *[_detail("f", "booting") for _ in range(50)]]
    )
    slow_source._api = slow_api  # type: ignore[attr-defined]
    slow = await slow_source.fork(wait_seconds=0.05)
    assert slow.state == "starting"


@pytest.mark.anyio
async def test_fork_takes_wait_seconds_not_timeout() -> None:
    sandbox = lazy_sandbox(_client(), "src")
    with pytest.raises(TypeError):
        await sandbox.fork(timeout=5)  # type: ignore[call-arg]
    with pytest.raises(BadRequestError, match="wait_seconds"):
        await AsyncSandbox._from_api(_detail("src", "running"), _Api([]), None).fork(wait_seconds=-1)  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["timeout", "terminal"])
async def test_create_returns_the_handle_however_its_wait_ends(ending: str) -> None:
    client = _client()
    if ending == "terminal":
        waits: list[object] = [_detail("sbx", "failed")]
    else:
        waits = [_detail("sbx", "booting") for _ in range(50)]
    client._api = _Api([{"id": "sbx", "status": "pending", "wait_cap_seconds": 0}, *waits])  # type: ignore[assignment]

    sandbox = await client.create("python:3.12", wait_seconds=1)

    assert sandbox.id == "sbx"
    assert sandbox.state == ("error" if ending == "terminal" else "starting")
    assert not hasattr(v2, "SandboxCreateTimeoutError")


@pytest.mark.anyio
async def test_create_sends_resources_as_whole_mib_and_user() -> None:
    client = _client()
    api = _Api([{"id": "sbx", "status": "running"}])
    client._api = api  # type: ignore[assignment]

    await client.create(
        "python:3.12",
        resources=Resources(cpu=1.5, cpu_max=2, memory=1.5, memory_max=2.25, disk=10, disk_max=10.5),
        user="worker",
    )

    body = api.specs[0].json_body
    assert body is not None
    assert {
        k: body[k] for k in ("cpu", "cores_max", "memory_mib", "memory_max_mib", "disk_mib", "disk_max_mib")
    } == {
        "cpu": 1.5,
        "cores_max": 2,
        "memory_mib": 1536,
        "memory_max_mib": 2304,
        "disk_mib": 10240,
        "disk_max_mib": 10752,
    }
    assert body["user"] == "worker"


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["memory", "memory_max", "disk", "disk_max"])
async def test_create_refuses_a_size_that_is_not_a_whole_number_of_mib(field: str) -> None:
    client = _client()
    api = _Api([])
    client._api = api  # type: ignore[assignment]
    with pytest.raises(BadRequestError, match=f"resources.{field} must be a GiB value"):
        await client.create("python:3.12", resources=Resources(**{field: 1.0001}))
    assert api.specs == []


@pytest.mark.anyio
async def test_create_has_one_resources_entry_and_no_toolbox_url() -> None:
    for method in (AsyncDaytona.create, AsyncDaytona.create_from_snapshot, AsyncDaytona.create_macos):
        parameters = inspect.signature(method).parameters
        assert not {"cpu", "memory", "disk", "toolbox_url"} & set(parameters), method.__name__
    assert "resources" not in inspect.signature(AsyncDaytona.create_from_snapshot).parameters
    assert "toolbox_url" in inspect.signature(AsyncDaytona.get).parameters
    client = _client()
    client._api = _Api([])  # type: ignore[assignment]
    with pytest.raises(BadRequestError, match="MacosResources"):
        await client.create_macos("base-macos", resources=Resources(cpu=4))  # type: ignore[arg-type]
    assert [f.name for f in dataclasses.fields(MacosResources)] == ["cpu", "memory", "disk"]
    with pytest.raises(TypeError):
        Resources(1, 2)  # type: ignore[misc]


def test_sizes_read_back_exactly_for_every_class() -> None:
    linux = AsyncSandbox._from_api(
        _detail("lin", "running", memory_mib=1536, disk_mib=10752, memory_max_mib=2304), None, None
    )
    assert (linux.memory, linux.memory_max, linux.disk) == (1.5, 2.25, 10.5)
    unsized = AsyncSandbox._from_api({"id": "x", "status": "running"}, None, None)
    assert (unsized.memory, unsized.disk) == (None, None)


def test_handle_metadata_is_read_only_in_both_surfaces() -> None:
    sandbox = AsyncSandbox._from_api(_detail("sbx", "running", labels={"team": "sdk"}), None, None)
    for name in (
        "id",
        "name",
        "state",
        "region",
        "labels",
        "cpu",
        "memory",
        "sandbox_class",
        "error",
        "user",
    ):
        with pytest.raises(AttributeError):
            setattr(sandbox, name, None)
    with pytest.raises(TypeError):
        sandbox.labels["team"] = "other"  # type: ignore[index]
    assert not hasattr(sandbox, "target")
    assert not hasattr(sandbox, "error_reason")
    assert not hasattr(sandbox, "from_api")
    assert not hasattr(sandbox, "wait_for_state")
    assert list(inspect.signature(AsyncSandbox.refresh_data).parameters) == ["self"]

    with Daytona(DaytonaConfig(api_key="key")) as client:
        sync_sandbox = lazy_sandbox(client, "sbx")
        with pytest.raises(AttributeError):
            sync_sandbox.state = "started"  # type: ignore[misc]
    # Readable after the client closed: a snapshot read, not a loop call.
    assert sync_sandbox.id == "sbx"
    assert list(inspect.signature(type(sync_sandbox).__init__).parameters) == ["self", "_impl", "_bridge"]


@pytest.mark.anyio
async def test_wait_accepts_one_state_or_several_and_validates_its_timeout() -> None:
    sandbox = AsyncSandbox._from_api(_detail("sbx", "running"), _Api([_detail("sbx", "paused")]), None)
    assert await sandbox.wait(until=["paused", "stopped"], timeout=5) is sandbox
    assert sandbox.state == "paused"
    with pytest.raises(BadRequestError, match="timeout"):
        await sandbox.wait(timeout=-1)


@pytest.mark.anyio
async def test_client_lifecycle_verbs_take_the_handle_options_and_no_policy_duplicates() -> None:
    for verb in ("start", "stop", "pause", "resume", "delete"):
        client_parameters = list(inspect.signature(getattr(AsyncDaytona, verb)).parameters)[2:]
        handle_parameters = list(inspect.signature(getattr(AsyncSandbox, verb)).parameters)[1:]
        assert client_parameters == handle_parameters == ["timeout"], verb
    for removed in (
        "get_network_policy",
        "update_network_policy",
        "reset_network_policy",
        "get_sandbox_secrets",
        "replace_sandbox_secrets",
    ):
        assert not hasattr(AsyncDaytona, removed)
        assert not hasattr(Daytona, removed)


@pytest.mark.anyio
async def test_client_accessors_and_service_classes_are_plural() -> None:
    client = _client()
    try:
        for accessor, cls in (
            ("images", "AsyncImagesService"),
            ("snapshots", "AsyncSnapshotsService"),
            ("volumes", "AsyncVolumesService"),
            ("secrets", "AsyncSecretsService"),
            ("reservations", "AsyncReservationsService"),
            ("mac_models", "AsyncMacModelsService"),
        ):
            assert type(getattr(client, accessor)).__name__ == cls
            assert hasattr(v2, cls) and hasattr(v2, cls.removeprefix("Async"))
        assert not hasattr(client, "volume") and not hasattr(client, "secret")
    finally:
        await client.close()
    for hidden in (
        "NAME_LABEL",
        "idempotency_key_for_name",
        "SandboxInfo",
        "ImageListItem",
        "SnapshotDetail",
    ):
        assert not hasattr(v2, hidden), hidden


@pytest.mark.anyio
async def test_volumes_create_disk_volumes_explicitly_and_delete_by_name_or_id() -> None:
    api = _Api(
        [
            {
                "id": "vol_1",
                "name": "data",
                "type": "disk",
                "status": "ready",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
            {},
        ]
    )
    volumes = AsyncVolumesService(api)  # type: ignore[arg-type]
    created = await volumes.create(name="data", size_gib=10)
    await volumes.delete("data", id_only=False)
    assert api.specs[0].json_body == {"name": "data", "type": "disk", "size_gib": 10}
    assert created.created_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert api.specs[1].path == "/volumes/data"
    assert list(inspect.signature(AsyncVolumesService.delete).parameters)[1] == "name_or_id"


@pytest.mark.anyio
async def test_secret_is_an_sdk_owned_frozen_model() -> None:
    api = _Api(
        [
            {
                "name": "TOKEN",
                "allowed_domains": ["*.example.com"],
                "description": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "future_field": 1,
            }
        ]
    )
    secret = await AsyncSecretsService(api).get("TOKEN")  # type: ignore[arg-type]
    assert secret == Secret(
        name="TOKEN",
        allowed_domains=("*.example.com",),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert not hasattr(secret, "to_dict") and not hasattr(secret, "additional_properties")
    with pytest.raises(dataclasses.FrozenInstanceError):
        secret.name = "OTHER"  # type: ignore[misc]


def test_image_builder_is_built_only_from_a_source() -> None:
    with pytest.raises(BadRequestError, match="from_registry"):
        ImageBuilder()
    builder = ImageBuilder.from_registry("python:3.12").run_commands("pip install x")
    assert builder.to_dockerfile() == "FROM python:3.12\nRUN pip install x\n"
    assert not hasattr(builder, "render_dockerfile") and not hasattr(builder, "build_request_source")


@pytest.mark.anyio
async def test_lifecycle_requests_reach_the_server_whatever_the_class() -> None:
    refusal = create_error("not supported", 409, "NOT_SUPPORTED_FOR_CLASS", "DAYTONA_API")
    for verb in ("stop", "pause", "resume"):
        api = _Api([refusal])
        mac = AsyncSandbox._from_api(
            _detail("mac", "running", **{"class": "macos", "persistent": False}), api, None
        )
        with pytest.raises(type(refusal)):
            await getattr(mac, verb)()
        assert api.specs[0].path == f"/sandboxes/mac/{verb}"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        create_error("upstream timed out", 408, "REQUEST_TIMEOUT", "DAYTONA_API"),
        create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"),
        ClientTimeoutError(),
        ConnectionError("connection reset", source="SDK"),
    ],
    ids=["server-408", "server-500", "client-timeout", "connection"],
)
async def test_a_failed_read_after_a_successful_create_or_fork_returns_the_handle(failure: Exception) -> None:
    # The create (or fork) request succeeded, so the sandbox exists: a failed
    # read during the wait never costs the caller its only reference to it.
    client = _client()
    client._api = _Api([{"id": "sbx", "status": "pending", "wait_cap_seconds": 0}, failure])  # type: ignore[assignment]
    sandbox = await client.create("python:3.12", wait_seconds=5)
    assert (sandbox.id, sandbox.state) == ("sbx", "creating")

    source = lazy_sandbox(_client(), "src")
    source._update(sandbox_class="macos")
    source._api = _Api([_fork_response("f"), _detail("f", "pending"), failure])  # type: ignore[attr-defined]
    fork = await source.fork(wait_seconds=5)
    assert (fork.id, fork.state) == ("f", "creating")


@pytest.mark.anyio
async def test_a_failed_fork_detail_read_returns_the_handle_from_the_fork_response() -> None:
    source = lazy_sandbox(_client(), "src")
    source._update(sandbox_class="macos")
    api = _Api([_fork_response("f"), create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API")])
    source._api = api  # type: ignore[attr-defined]

    fork = await source.fork(wait_seconds=5)

    assert (fork.id, fork.state, fork.forked_from, fork.sandbox_class) == ("f", "creating", "src", "macos")
    # No wait follows an unreadable fork: the two requests are all there is.
    assert [(spec.method, spec.path) for spec in api.specs] == [
        ("POST", "/sandboxes/src/fork"),
        ("GET", "/sandboxes/f"),
    ]


@pytest.mark.anyio
async def test_a_server_408_on_the_create_post_is_the_servers_error() -> None:
    client = _client()
    client._api = _Api([create_error("upstream timed out", 408, "REQUEST_TIMEOUT", "DAYTONA_API")])  # type: ignore[assignment]
    with pytest.raises(RequestTimeoutError) as raised:
        await client.create("python:3.12", wait_seconds=5)
    assert not isinstance(raised.value, SandboxCreateRequestTimeoutError)
    assert (raised.value.source, raised.value.message) == ("DAYTONA_API", "upstream timed out")


@pytest.mark.anyio
async def test_a_failed_create_post_raises() -> None:
    client = _client()
    client._api = _Api([create_error("over the limit", 403, "FORBIDDEN", "DAYTONA_API")])  # type: ignore[assignment]
    with pytest.raises(ForbiddenError):
        await client.create("python:3.12", wait_seconds=5)


class _ResolvingHub:
    """An events hub that reports the target state at once, as a socket frame would."""

    enabled = True

    def __init__(self, state: str, raw_state: str) -> None:
        self.outcome = WaitOutcome(state, raw_state)  # type: ignore[arg-type]
        self.region_hints: list[Optional[str]] = []

    async def wait(
        self,
        sandbox_id: str,
        predicate: object,
        *,
        region_hint: Optional[str],
        on_observed: Callable[[WaitOutcome], None],
        **_options: object,
    ) -> WaitOutcome:
        self.region_hints.append(region_hint)
        on_observed(self.outcome)
        return self.outcome


@pytest.mark.anyio
@pytest.mark.parametrize("verb", ["wait", "start", "stop"])
async def test_an_observed_target_survives_a_failed_metadata_refresh(verb: str) -> None:
    target = {
        "wait": ("started", "running"),
        "start": ("started", "running"),
        "stop": ("stopped", "stopped"),
    }[verb]
    sandbox = lazy_sandbox(_client(), "sbx")
    sandbox._update(sandbox_class="linux", state="stopped" if verb == "start" else "started", raw_state="x")
    # start() reads the current state first; start() and stop() then send their request.
    responses: list[object] = {"wait": [], "start": [_detail("sbx", "stopped"), {}], "stop": [{}]}[verb]
    # The follow-up read that fills in the rest of the metadata fails.
    responses.append(create_error("boom", 500, "INTERNAL_SERVER_ERROR", "DAYTONA_API"))
    sandbox._api = _Api(responses)  # type: ignore[attr-defined]
    sandbox._hub = _ResolvingHub(*target)  # type: ignore[assignment]

    await getattr(sandbox, verb)(timeout=5)

    assert sandbox.state == target[0]
    assert sandbox._api.responses == []  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_local_is_never_a_region_hint() -> None:
    sandbox = AsyncSandbox._from_api(
        _detail("sbx", "running", region_id="Local"), _Api([_detail("sbx", "running")]), None
    )
    hub = _ResolvingHub("started", "running")
    sandbox._hub = hub  # type: ignore[assignment]
    await sandbox.wait(timeout=5)
    assert hub.region_hints == [None]


@pytest.mark.anyio
async def test_a_successful_refresh_after_an_observed_target_keeps_what_it_read() -> None:
    sandbox = lazy_sandbox(_client(), "sbx")
    sandbox._update(sandbox_class="linux", state="starting", raw_state="booting")
    sandbox._api = _Api([_detail("sbx", "stopping")])  # type: ignore[attr-defined]
    sandbox._hub = _ResolvingHub("started", "running")  # type: ignore[assignment]

    await sandbox.wait(timeout=5)

    # The handle reports what the server said last, not the event-time state.
    assert (sandbox.state, sandbox.raw_state) == ("stopping", "stopping")


@pytest.mark.anyio
@pytest.mark.parametrize("response", [{"status": "pending"}, {"id": "", "status": "pending"}, {"id": 5}])
async def test_a_create_response_without_the_sandbox_id_is_a_contract_error(
    response: dict[str, object],
) -> None:
    client = _client()
    api = _Api([response])
    client._api = api  # type: ignore[assignment]
    with pytest.raises(BadRequestError, match="sandbox create response is missing the sandbox id") as raised:
        await client.create("img_1", wait_seconds=5)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    # Nothing follows: no read of a sandbox called "None".
    assert [spec.method for spec in api.specs] == ["POST"]
