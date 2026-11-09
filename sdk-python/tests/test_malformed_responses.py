# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The malformed-response rule for the client, lifecycle and platform-service area.

A ``{}`` body is a :class:`BadRequestError` (400, ``BAD_REQUEST``, ``SDK``)
naming what is missing, and nothing follows it (no ``GET /images/None``, no
fork handle). The TypeScript twin is ``tests/malformed-responses.test.ts``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timezone

import anyio
import pytest
from _handles import lazy_sandbox
from network_policy_test_server import FakeApi, PlannedResponse

from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import BadRequestError, Image, ImageBuilder, Snapshot
from daytona.v2.daytona import AsyncDaytona as AsyncDaytonaV2
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import JsonObject, JsonValue

pytestmark = pytest.mark.anyio


@contextlib.contextmanager
def _sdk_bad_request(match: str) -> Iterator[None]:
    """The malformed-response error the SDK raises itself (source ``SDK``), so a
    server-side ``BAD_REQUEST`` cannot satisfy the assertion."""
    with pytest.raises(BadRequestError, match=match) as raised:
        yield
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


_DETAIL: JsonObject = {
    "id": "src",
    "class": "linux",
    "name": "src",
    "status": "stopped",
    "cpu": 1,
    "cores_max": 1,
    "memory_mib": 1024,
    "disk_mib": 1024,
    "port_map": [],
    "region_id": "region-a",
}

Call = Callable[[AsyncDaytonaV2], Awaitable[object]]

_CALLS: list[tuple[str, Call, str, int]] = [
    ("get", lambda d: d.get("sbx"), "id", 0),
    ("images.get", lambda d: d.images.get("img"), "id", 0),
    ("images.build", lambda d: d.images.build(ImageBuilder.from_registry("alpine")), "id", 0),
    ("images.pull", lambda d: d.images.pull("alpine", wait=False), "id", 0),
    ("snapshots.get", lambda d: d.snapshots.get("snap"), "id", 0),
    ("volumes.get", lambda d: d.volumes.get("data"), "id", 0),
    ("volumes.create", lambda d: d.volumes.create(name="data"), "id", 0),
    ("secrets.get", lambda d: d.secrets.get("KEY"), "name", 0),
    ("secrets.set", lambda d: d.secrets.set("KEY", "value", allowed_domains=[]), "name", 0),
    ("reservations.get", lambda d: d.reservations.get("res"), "id", 0),
    ("reservations.create", lambda d: d.reservations.create(region="mac"), "id", 0),
    ("reservations.create_bulk", lambda d: d.reservations.create_bulk(region="mac", count=2), "requested", 0),
    ("reservations.renew", lambda d: d.reservations.renew("res"), "id", 0),
    ("reservations.update", lambda d: d.reservations.update("res", auto_renew=False), "id", 0),
    ("mac_models.catalog", lambda d: d.mac_models.catalog(), "regions", 0),
    ("current_api_key", lambda d: d.current_api_key(), "id", 0),
    ("list_audit_actions", lambda d: d.list_audit_actions(), "targets", 0),
    ("get_limits", lambda d: d.get_limits(organization_id="org"), "sandboxes", 0),
    # The sandbox operations first read the source's class.
    # wait_seconds=0 is one request: no class read first.
    ("fork", lambda d: lazy_sandbox(d, "src").fork(wait_seconds=0), "id", 0),
    ("capture_image", lambda d: lazy_sandbox(d, "src").capture_image(), "id", 0),
    ("create_snapshot", lambda d: lazy_sandbox(d, "src").create_snapshot("snap"), "id", 0),
    ("create_access_token", lambda d: lazy_sandbox(d, "src").create_access_token(), "access_token", 0),
    ("ports.expose", lambda d: lazy_sandbox(d, "src").ports.expose(8080), "port", 0),
    ("ports.signed_preview_link", lambda d: lazy_sandbox(d, "src").ports.signed_preview_link(8080), "url", 0),
]


@pytest.mark.parametrize(("name", "call", "field", "reads_first"), _CALLS, ids=[case[0] for case in _CALLS])
async def test_an_empty_body_is_a_contract_error_and_nothing_follows(
    name: str, call: Call, field: str, reads_first: int
) -> None:
    del name
    planned = [PlannedResponse(200, _DETAIL)] * reads_first + [PlannedResponse(200, {})]
    with FakeApi(planned) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError, match=r"response") as raised:
                await call(root.v2)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert len(api.requests) == reads_first + 1


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"access_token": "tok", "token_type": "Bearer", "expires_at": "tomorrow", "operations": []},
            "sandbox access token response has an expires_at that is not a timestamp",
        ),
        (
            {
                "access_token": "",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [],
            },
            "sandbox access token response has an empty access_token",
        ),
        (
            {
                "access_token": "tok",
                "token_type": "Bearer",
                "expires_at": "2099-01-01T00:00:00Z",
                "operations": [1],
            },
            r"sandbox access token response has a operations\[0\] that is not a string",
        ),
    ],
)
async def test_a_malformed_access_token_is_a_contract_error(body: dict[str, object], message: str) -> None:
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(f"^{message}$"):
                await lazy_sandbox(root.v2, "src").create_access_token()


async def test_a_detail_that_breaks_the_contract_leaves_the_list_item() -> None:
    with FakeApi(
        [
            PlannedResponse(
                200,
                {
                    "sandboxes": [{**_DETAIL, "id": "sbx", "status": "running", "cursor": "c1"}],  # type: ignore[dict-item]
                    "next_cursor": None,
                },
            ),
            PlannedResponse(200, ["not a sandbox"]),
        ]
    ) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            page = await root.v2.list()
    assert [(sandbox.id, sandbox.state) for sandbox in page.items] == [("sbx", "started")]
    assert [request.path for request in api.requests] == ["/sandboxes", "/sandboxes/sbx"]


_BUDGET_NULLS: JsonObject = {
    "monthly_limit_cents": None,
    "period_start_date": None,
    "created_at": None,
    "updated_at": None,
}
_USAGE: JsonObject = {"limit": None, "usage": 1}
_LIMITS: JsonObject = {
    "sandboxes": {"concurrent": _USAGE, "total": _USAGE},
    "image_builds": {"concurrent": _USAGE},
    "storage": {"volumes": _USAGE, "images": _USAGE, "snapshots": _USAGE},
    "secrets_per_project": _USAGE,
    "api_keys_per_project": _USAGE,
    "api_keys_per_organization": _USAGE,
}
_MODEL: JsonObject = {
    "id": "m4",
    "name": "Apple M4",
    "cores": 10,
    "memory_mib": 16384,
    "disk_mib": 244198,
    "sandbox_disk_mib": 235520,
    "max_sandboxes": 2,
    "default_size": None,
}
_NO_DEFAULT_SIZE: JsonObject = {k: v for k, v in _MODEL.items() if k != "default_size"}
_CATALOG_NO_PRICE: JsonObject = {
    "regions": [{"id": "r", "default_model": "m4", "models": [{"model": _MODEL, "available": 1}]}]
}

_NULLABLE: list[tuple[str, Call, JsonObject, str]] = [
    ("budget {}", lambda d: d.get_project_budget(), {}, "created_at"),
    (
        "budget without updated_at",
        lambda d: d.get_project_budget(),
        {k: v for k, v in _BUDGET_NULLS.items() if k != "updated_at"},
        "updated_at",
    ),
    (
        "usage limit",
        lambda d: d.get_limits(organization_id="org"),
        {**_LIMITS, "secrets_per_project": {"usage": 1}},
        r"secrets_per_project\.limit",
    ),
    ("Mac model", lambda d: d.mac_models.list(), {"mac_models": [_NO_DEFAULT_SIZE]}, r"default_size"),
    ("Mac price", lambda d: d.mac_models.catalog(), _CATALOG_NO_PRICE, r"price"),
]


@pytest.mark.parametrize(("name", "call", "body", "field"), _NULLABLE, ids=[case[0] for case in _NULLABLE])
async def test_a_required_nullable_key_must_be_present(
    name: str, call: Call, body: JsonObject, field: str
) -> None:
    # The contract allows an explicit null, not a missing key (as in TypeScript).
    del name
    with FakeApi([PlannedResponse(200, body)]) as api:  # type: ignore[arg-type]
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError, match=field) as raised:
                await call(root.v2)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


_EXPLICIT_NULLS: list[tuple[str, Call, JsonObject]] = [
    ("budget", lambda d: d.get_project_budget(), _BUDGET_NULLS),
    ("limits", lambda d: d.get_limits(organization_id="org"), _LIMITS),
    ("Mac models", lambda d: d.mac_models.list(), {"mac_models": [_MODEL]}),
    # The registry is not paged: a next_cursor there is an unknown field, ignored.
    (
        "Mac models with an unknown next_cursor",
        lambda d: d.mac_models.list(),
        {"mac_models": [_MODEL], "next_cursor": 5},
    ),
    (
        "Mac catalog",
        lambda d: d.mac_models.catalog(),
        {
            "regions": [
                {
                    "id": "r",
                    "default_model": "m4",
                    "models": [{"model": _MODEL, "available": 1, "price": None}],
                }
            ]
        },
    ),
]


@pytest.mark.parametrize(("name", "call", "body"), _EXPLICIT_NULLS, ids=[case[0] for case in _EXPLICIT_NULLS])
async def test_an_explicit_null_is_valid_where_the_contract_allows_it(
    name: str, call: Call, body: JsonObject
) -> None:
    del name
    with FakeApi([PlannedResponse(200, body)]) as api:  # type: ignore[arg-type]
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            assert await call(root.v2) is not None


async def test_a_non_string_label_is_a_contract_error() -> None:
    with FakeApi(
        [
            PlannedResponse(200, {**_DETAIL, "labels": {"team": 1}}),  # type: ignore[dict-item]
            PlannedResponse(200, {"id": "src", "labels": {"team": 1}}),
        ]
    ) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request("labels"):
                await root.v2.get("src")
            with _sdk_bad_request(r"^sandbox labels response has a labels\.team that is not a string$"):
                await lazy_sandbox(root.v2, "src").set_labels(labels={"team": "a"})


@pytest.mark.parametrize("call", ["get_network_policy", "get_secrets"])
async def test_a_policy_body_that_is_not_json_is_a_contract_error(call: str) -> None:
    with FakeApi(
        [PlannedResponse(200, b"<html>", {"ETag": '"e"', "Content-Type": "application/json"})]
    ) as api:  # type: ignore[arg-type]
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError, match="is not JSON") as raised:
                await getattr(lazy_sandbox(root.v2, "src"), call)()
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.parametrize("port", [2**31, 10**20])
async def test_a_port_beyond_int32_is_a_contract_error(port: int) -> None:
    with FakeApi([PlannedResponse(200, {"ports": [3000, port]})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "src", toolbox_url=api.url, access_token="tok")
            with _sdk_bad_request(r"ports\[1\] that is not a 32-bit integer"):
                await sandbox.ports.list()


async def test_a_network_policy_body_nested_too_deeply_names_the_response() -> None:
    body = ("[" * 100_000 + "]" * 100_000).encode()
    with FakeApi([PlannedResponse(200, body, {"ETag": '"e"'})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(
                BadRequestError, match="^expected network policy response: the JSON is nested too deeply$"
            ):
                await lazy_sandbox(root.v2, "src").get_network_policy()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_a_network_policy_body_with_a_non_json_constant_names_the_response(constant: str) -> None:
    # Not JSON (JSON.parse refuses them too): the malformed-response error.
    body = f'{{"block_all": false, "allow_cidrs": [], "allow_domains": [], "x": {constant}}}'.encode()
    with FakeApi([PlannedResponse(200, body, {"ETag": '"e"'})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request("^network policy response is not JSON$"):
                await lazy_sandbox(root.v2, "src").get_network_policy()


@pytest.mark.parametrize(
    ("labels", "field"),
    [({"team": 1}, r"labels\.team"), ("team", r"labels that is not an object")],
    ids=["value", "map"],
)
async def test_a_sandbox_object_with_malformed_labels_is_a_contract_error(
    labels: JsonValue, field: str
) -> None:
    # Every path that builds a handle from a sandbox object, as in TypeScript.
    with _sdk_bad_request(field):
        AsyncSandbox._from_api({**_DETAIL, "labels": labels}, None, None)


@pytest.mark.parametrize("key", ["next_cursor", "nextCursor"])
async def test_a_list_cursor_of_the_wrong_type_is_a_contract_error(key: str) -> None:
    with FakeApi([PlannedResponse(200, {"sandboxes": [], key: 5})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(f"has a {key} that is not a string"):
                await root.v2.list()


# Toolbox reads this area maps (the TypeScript twin covers the same four): a
# `{}` body is the malformed-response error, and a waiting Docker read fails at
# once rather than at its deadline.
_TOOLBOX_CALLS: list[tuple[str, Callable[[AsyncSandbox], Awaitable[object]]]] = [
    ("docker.status", lambda s: s.docker.status()),
    ("docker.wait_until_ready", lambda s: s.docker.wait_until_ready(timeout=30)),
    ("volumes.pull", lambda s: s.volumes.pull("vol1", request_id="r1")),
    ("ports.is_in_use", lambda s: s.ports.is_in_use(3000)),
    ("metrics", lambda s: s.metrics()),
]


@pytest.mark.parametrize(("name", "call"), _TOOLBOX_CALLS, ids=[case[0] for case in _TOOLBOX_CALLS])
async def test_an_empty_toolbox_body_is_a_contract_error(
    name: str, call: Callable[[AsyncSandbox], Awaitable[object]]
) -> None:
    del name
    with FakeApi([PlannedResponse(200, {})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "src", toolbox_url=api.url, access_token="tok")
            with anyio.fail_after(5), pytest.raises(BadRequestError) as raised:
                await call(sandbox)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "id"),
        ({"id": "src"}, "lifecycle_policy"),
        ({"id": "src", "lifecycle_policy": {"timeout_seconds": "60"}}, "timeout_seconds"),
        ([1], "is not a JSON object"),
    ],
    ids=["empty", "no lifecycle_policy", "timeout_seconds a string", "not an object"],
)
@pytest.mark.parametrize("verb", ["update_lifecycle_policy", "reset_idle_timer"])
async def test_a_malformed_lifecycle_echo_is_a_contract_error(verb: str, body: JsonValue, field: str) -> None:
    # The TypeScript twin covers the same echoes.
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = lazy_sandbox(root.v2, "src")
            sandbox._update(sandbox_class="linux", persistent=False)
            with _sdk_bad_request(f"sandbox lifecycle policy response.*{field}"):
                if verb == "reset_idle_timer":
                    await sandbox.reset_idle_timer()
                else:
                    await sandbox.update_lifecycle_policy(timeout_seconds=60)
    assert len(api.requests) == 1


# The create response's `wait_cap_seconds` is read, never refused (the sandbox
# exists), exactly as TypeScript reads it: a finite number, a negative one
# clamped to 0, anything else 0. With a 5 s wait, a cap that covers it needs no
# further read; a cap read as 0 (or short of it) leaves a wait read, answered
# `running`. The TypeScript twin pins the same rows.
_RUNNING_DETAIL = json.dumps({**_DETAIL, "status": "running"}).encode()
_WAIT_CAPS: list[tuple[str, str, int]] = [
    ("5", "5", 1),
    ("5.0", "5.0", 1),
    ("7", "7", 1),
    ("4.5", "4.5", 2),
    ("-3", "-3", 2),
    ('"5"', '"5"', 2),
    ("null", "null", 2),
    ("1e400", "1e400", 2),
    ("10**400", "1" + "0" * 400, 2),
]


@pytest.mark.parametrize(("label", "raw", "requests"), _WAIT_CAPS, ids=[case[0] for case in _WAIT_CAPS])
async def test_the_create_wait_cap_is_read_as_typescript_reads_it(
    label: str, raw: str, requests: int
) -> None:
    del label
    created = f'{{"id": "src", "status": "pending", "wait_cap_seconds": {raw}}}'.encode()
    with FakeApi([PlannedResponse(200, created), PlannedResponse(200, _RUNNING_DETAIL)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with anyio.fail_after(10):
                await root.v2.create("img_1", wait_seconds=5)
    assert len(api.requests) == requests


_HUGE = "1" + "0" * 400
_NETWORK_POLICY = (
    '{{"desired": {desired}, "desired_revision": {desired_revision}, "applied": null, '
    '"applied_revision": {applied_revision}, "applied_start_id": null, "status": "applied", "error": null}}'
)
_POLICY = '{{"version": {version}, "block_all": false, "allow_cidrs": [], "allow_domains": []}}'
_SECRETS = (
    '{{"secrets": [], "desired_revision": {desired_revision}, "applied_revision": {applied_revision}, '
    '"status": "pending", "error": null, "capture_status": null, "capture_unresolved_count": {count}}}'
)

# An integer a double cannot carry (TypeScript's JSON.parse reads it as
# Infinity and refuses it) is the malformed-response error naming the field, in
# the hand-written mappers; the TypeScript twin pins the same rows.
_HUGE_INTEGERS: list[tuple[str, str, Callable[[AsyncSandbox], Awaitable[object]], str]] = [
    (
        "network policy desired_revision",
        _NETWORK_POLICY.format(desired="null", desired_revision=_HUGE, applied_revision="null"),
        lambda s: s.get_network_policy(),
        "desired_revision",
    ),
    (
        "network policy applied_revision",
        _NETWORK_POLICY.format(desired="null", desired_revision=1, applied_revision=_HUGE),
        lambda s: s.get_network_policy(),
        "applied_revision",
    ),
    (
        "network policy desired.version",
        _NETWORK_POLICY.format(
            desired=_POLICY.format(version=_HUGE), desired_revision=1, applied_revision="null"
        ),
        lambda s: s.get_network_policy(),
        "desired.version",
    ),
    (
        "secrets desired_revision",
        _SECRETS.format(desired_revision=_HUGE, applied_revision="null", count="null"),
        lambda s: s.get_secrets(),
        "desired_revision",
    ),
    (
        "secrets applied_revision",
        _SECRETS.format(desired_revision=1, applied_revision=_HUGE, count="null"),
        lambda s: s.get_secrets(),
        "applied_revision",
    ),
    (
        "secrets capture_unresolved_count",
        _SECRETS.format(desired_revision=1, applied_revision="null", count=_HUGE),
        lambda s: s.get_secrets(),
        "capture_unresolved_count",
    ),
    (
        "access token ports[0]",
        f'{{"access_token": "t", "token_type": "Bearer", "expires_at": "2030-01-01T00:00:00Z", "operations": [], "ports": [{_HUGE}]}}',
        lambda s: s.create_access_token(),
        r"ports\[0\]",
    ),
]


@pytest.mark.parametrize(
    ("name", "body", "call", "field"), _HUGE_INTEGERS, ids=[case[0] for case in _HUGE_INTEGERS]
)
async def test_an_integer_a_double_cannot_carry_is_a_contract_error(
    name: str, body: str, call: Callable[[AsyncSandbox], Awaitable[object]], field: str
) -> None:
    del name
    with FakeApi([PlannedResponse(200, body.encode(), {"ETag": '"e"'})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with pytest.raises(BadRequestError, match=field) as raised:
                await call(lazy_sandbox(root.v2, "src"))
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


async def test_a_search_total_a_double_cannot_carry_is_a_contract_error() -> None:
    body = f'{{"sandboxes": [], "total": {_HUGE}, "total_relation": "eq", "next_cursor": null}}'.encode()
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request("has a total that is not an integer"):
                await root.v2.search()


@pytest.mark.parametrize("call", ["search", "find_by_name"])
async def test_a_negative_search_total_is_a_contract_error(call: str) -> None:
    # The contract's total is an int64 with minimum 0.
    body = {"sandboxes": [], "total": -1, "total_relation": "eq", "next_cursor": None}
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request("search response has a negative total"):
                await (root.v2.search() if call == "search" else root.v2.find_by_name("n"))
    assert len(api.requests) == 1


async def test_a_search_total_above_2_53_that_a_double_carries_is_accepted() -> None:
    body = {"sandboxes": [], "total": 2**60, "total_relation": "eq", "next_cursor": None}
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            page = await root.v2.search()
    assert page.total == 2**60


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"sandboxes": [], "total_relation": "eq", "next_cursor": None},
            "search response is missing its total",
        ),
        ({"sandboxes": [], "total": 0, "next_cursor": None}, "search response is missing its total_relation"),
    ],
    ids=["no total", "no total_relation"],
)
async def test_find_by_name_checks_every_page_as_search_does(body: JsonObject, message: str) -> None:
    # The TypeScript twin checks the same pages.
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(f"^{message}$"):
                await root.v2.find_by_name("x")


_IMAGE: JsonObject = {
    "id": "img",
    "class": "linux",
    "status": "ready",
    "region_id": "r",
    "region_ids": ["r"],
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


@pytest.mark.parametrize("flag", ["platform", "default", "deprecated"])
@pytest.mark.parametrize("broken", ["missing", "not a boolean"])
async def test_an_image_without_a_required_flag_is_a_contract_error(flag: str, broken: str) -> None:
    # The contract requires all three; the TypeScript twin refuses the same.
    body = {key: value for key, value in _IMAGE.items() if key != flag}
    if broken != "missing":
        body[flag] = "yes"
    with FakeApi(
        [
            PlannedResponse(200, body),
            PlannedResponse(200, {"images": [{**body, "cursor": "c"}], "next_cursor": None}),
        ]
    ) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(flag):
                await root.v2.images.get("img")
            with _sdk_bad_request(flag):
                await root.v2.images.list()


# A sandbox detail's lifecycle policy is checked field by field, as
# TypeScript checks it (tests/malformed-responses.test.ts): integers, actions
# any string (an unknown one kept), deadlines strings.
@pytest.mark.parametrize(
    ("policy", "field"),
    [
        ({"timeout_seconds": "60"}, "timeout_seconds"),
        ({"on_timeout": 5}, "on_timeout"),
        ({"timeout_at": 5}, "lifecycle_policy.timeout_at"),
    ],
    ids=["string timeout_seconds", "numeric on_timeout", "numeric timeout_at"],
)
@pytest.mark.parametrize("call", ["get", "list"])
async def test_a_malformed_lifecycle_policy_field_is_a_contract_error(
    policy: JsonObject, field: str, call: str
) -> None:
    detail = {**_DETAIL, "lifecycle_policy": policy}
    body: JsonObject = detail if call == "get" else {"sandboxes": [detail], "next_cursor": None}
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(field):
                await (root.v2.get("src") if call == "get" else root.v2.list())
    assert len(api.requests) == 1


async def test_an_unknown_lifecycle_action_is_accepted() -> None:
    detail = {**_DETAIL, "lifecycle_policy": {"on_timeout": "hibernate", "timeout_seconds": 60}}
    with FakeApi([PlannedResponse(200, detail)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = await root.v2.get("src")
    policy = sandbox.lifecycle_policy
    assert policy is not None and policy.on_timeout == "hibernate"


_SNAPSHOT: JsonObject = {
    "id": "snap",
    "name": "saved",
    "class": "linux",
    "kind": "full",
    "status": "ready",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "region_id": "r",
    "region_ids": ["r"],
}


# A timestamp the contract types as a string is refused when it is a number,
# as TypeScript refuses it (the generated models read a number as a Unix time).
@pytest.mark.parametrize(
    ("call", "body", "field"),
    [
        ("images.get", {**_IMAGE, "created_at": 5}, "created_at"),
        ("images.get", {**_IMAGE, "expires_at": 5}, "expires_at"),
        (
            "images.list",
            {"images": [{**_IMAGE, "cursor": "c", "updated_at": 1.5}], "next_cursor": None},
            "images[0].updated_at",
        ),
        ("snapshots.get", {**_SNAPSHOT, "created_at": 5}, "created_at"),
        (
            "snapshots.list",
            {"snapshots": [{**_SNAPSHOT, "cursor": "c", "updated_at": 5}], "next_cursor": None},
            "snapshots[0].updated_at",
        ),
    ],
    ids=[
        "image created_at",
        "image expires_at",
        "image list updated_at",
        "snapshot created_at",
        "snapshot list",
    ],
)
async def test_a_numeric_artifact_timestamp_is_a_contract_error(
    call: str, body: JsonObject, field: str
) -> None:
    calls: dict[str, Call] = {
        "images.get": lambda d: d.images.get("img"),
        "images.list": lambda d: d.images.list(),
        "snapshots.get": lambda d: d.snapshots.get("snap"),
        "snapshots.list": lambda d: d.snapshots.list(),
    }
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(re.escape(f"has a {field} that is not a string")):
                await calls[call](root.v2)
    assert len(api.requests) == 1


_MISSING = object()


@pytest.mark.parametrize(
    "status", [5, None, ["running"], _MISSING], ids=["number", "null", "array", "missing"]
)
@pytest.mark.parametrize("call", ["create", "fork"])
async def test_a_create_or_fork_status_that_is_not_a_string_reads_as_unknown(
    call: str, status: object
) -> None:
    # The sandbox exists once its id came back: create and fork return the
    # handle, its state `unknown`, and raise only when the request failed or
    # no id came back. The TypeScript twin is in malformed-responses.test.ts.
    body: JsonObject = {"id": "sbx"}
    if call == "fork":
        body.update({"forked_from": "src", "toolbox_proxy_url": "https://p.example"})
    if status is not _MISSING:
        body["status"] = status  # type: ignore[assignment]
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            if call == "create":
                handle = await root.v2.create("img_1", wait_seconds=0)
            else:
                handle = await lazy_sandbox(root.v2, "src").fork(wait_seconds=0)
    assert handle.id == "sbx"
    assert handle.state == "unknown"
    assert len(api.requests) == 1


_PORT: JsonObject = {
    "port": 8080,
    "public": False,
    "url": "https://8080-src.preview.invalid",
    "created_at": "2026-01-01T00:00:00Z",
}


# Every date-time a generated API model reads is checked the same way, on the
# sandbox, preview endpoint and signed preview link responses too.
@pytest.mark.parametrize(
    ("call", "body", "path"),
    [
        (lambda d: lazy_sandbox(d, "src").ports.expose(8080), {**_PORT, "created_at": 5}, "created_at"),
        (
            lambda d: lazy_sandbox(d, "src").ports.list_endpoints(),
            {"ports": [{**_PORT, "created_at": 1.5}], "next_cursor": None},
            "ports[0].created_at",
        ),
        (
            lambda d: lazy_sandbox(d, "src").ports.signed_preview_link(8080),
            {"url": "https://s.preview.invalid", "token": "s" + "0" * 32, "expires_at": 5},
            "expires_at",
        ),
        (
            lambda d: d.list(),
            {"sandboxes": [{**_DETAIL, "updated_at": 5}], "next_cursor": None},
            "updated_at",
        ),
    ],
    ids=["preview endpoint", "preview endpoint list", "signed preview link", "sandbox list"],
)
async def test_a_numeric_timestamp_is_a_contract_error_on_every_model(
    call: Call, body: JsonObject, path: str
) -> None:
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request(re.escape(f"has a {path} that is not a string")):
                await call(root.v2)
    assert len(api.requests) == 1


@pytest.mark.parametrize("cursor", ["missing", 5], ids=["missing", "not a string"])
@pytest.mark.parametrize("call", ["images.list", "snapshots.list"])
async def test_a_list_item_cursor_is_not_read(call: str, cursor: object) -> None:
    # Neither SDK reads the per-item cursor (next_cursor continues the list),
    # so a missing or mistyped one is not a contract error, as in TypeScript.
    key, item = ("images", _IMAGE) if call == "images.list" else ("snapshots", _SNAPSHOT)
    entry: JsonObject = dict(item) if cursor == "missing" else {**item, "cursor": 5}
    with FakeApi([PlannedResponse(200, {key: [entry], "next_cursor": None})]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            service = root.v2.images if call == "images.list" else root.v2.snapshots
            page = await service.list()
    assert len(page.items) == 1


async def test_a_signed_preview_link_without_expires_at_is_a_contract_error() -> None:
    # The contract requires expires_at, so the link always carries it; the
    # TypeScript twin is in malformed-responses.test.ts.
    body: JsonObject = {"url": "https://s.preview.invalid", "token": "s" + "0" * 32}
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request("expires_at: Field required"):
                await lazy_sandbox(root.v2, "src").ports.signed_preview_link(8080)


async def test_a_signed_preview_link_expiry_without_an_offset_is_utc() -> None:
    body: JsonObject = {
        "url": "https://s.preview.invalid",
        "token": "s" + "0" * 32,
        "expires_at": "2030-01-01T00:00:00",
    }
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            link = await lazy_sandbox(root.v2, "src").ports.signed_preview_link(8080)
    assert link.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


async def test_an_integer_too_large_for_a_double_in_a_mapped_field_is_a_contract_error() -> None:
    # TypeScript's JSON.parse reads it as Infinity, which its integer check
    # refuses; Python refuses the same field before the model parse. The
    # TypeScript twin is in malformed-responses.test.ts.
    body = json.dumps({**_IMAGE, "bytes": 1}).replace('"bytes": 1', '"bytes": 1' + "0" * 400)
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            with _sdk_bad_request("^image detail response has a bytes that is not an integer$"):
                await root.v2.images.get("img")


_FORK_URL = "https://toolbox.example/fork"
_FORK_BODIES: dict[str, JsonObject] = {
    "id only": {"id": "f1"},
    "no forked_from": {"id": "f1", "status": "pending", "toolbox_proxy_url": _FORK_URL},
    "no toolbox_proxy_url": {"id": "f1", "status": "pending", "forked_from": "src"},
    "wrong types": {"id": "f1", "status": 5, "forked_from": 7, "toolbox_proxy_url": ["x"]},
}


@pytest.mark.parametrize("wait_seconds", [0, 5])
@pytest.mark.parametrize("body", list(_FORK_BODIES))
async def test_a_fork_response_with_only_its_id_returns_the_handle(body: str, wait_seconds: int) -> None:
    # The fork exists once its id came back: fork() returns the handle, with
    # the source as `forked_from` and the toolbox URL from the fork's detail
    # read, never the source's. The TypeScript twin is in
    # malformed-responses.test.ts.
    detail: JsonObject = {
        **_DETAIL,
        "id": "f1",
        "forked_from": "src",
        "toolbox_proxy_url": "https://toolbox.example/detail",
    }
    planned = [PlannedResponse(200, _FORK_BODIES[body])]
    if wait_seconds:
        planned = [PlannedResponse(200, _DETAIL), *planned, PlannedResponse(200, detail)]
    with FakeApi(planned) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        os.environ["DAYTONA_SANDBOX_EVENTS"] = "0"
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            fork = await lazy_sandbox(root.v2, "src").fork(wait_seconds=wait_seconds)
    assert (fork.id, fork.forked_from) == ("f1", "src")
    given = _FORK_BODIES[body].get("toolbox_proxy_url")
    if isinstance(given, str):
        assert fork._toolbox_proxy_url == f"{_FORK_URL}/f1"
    elif wait_seconds:
        assert fork._toolbox_proxy_url == "https://toolbox.example/detail/f1"
    else:
        assert fork._toolbox is None
    assert [request.method for request in api.requests] == (
        ["GET", "POST", "GET"] if wait_seconds else ["POST"]
    )


_ACCEPTED: list[tuple[str, Call, JsonObject]] = [
    ("images.build", lambda d: d.images.build(ImageBuilder.from_registry("alpine")), {"id": "img"}),
    ("images.pull", lambda d: d.images.pull("alpine", wait=False), {"id": "img"}),
    ("capture_image", lambda d: lazy_sandbox(d, "src").capture_image(), {"id": "img", "status": 5}),
    ("create_snapshot", lambda d: lazy_sandbox(d, "src").create_snapshot("snap"), {"id": "snap"}),
]


@pytest.mark.parametrize(("name", "call", "accepted"), _ACCEPTED, ids=[case[0] for case in _ACCEPTED])
async def test_an_accepted_create_needs_only_its_id(name: str, call: Call, accepted: JsonObject) -> None:
    # The artifact exists once its id came back: the call follows the id
    # whatever the acknowledgement's status. Twin in malformed-responses.test.ts.
    created = _SNAPSHOT if name == "create_snapshot" else _IMAGE
    with FakeApi([PlannedResponse(200, accepted), PlannedResponse(200, created)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            result = await call(root.v2)
    assert isinstance(result, (Image, Snapshot)) and result.id == created["id"]
    assert len(api.requests) == 2


async def test_a_create_response_with_mistyped_optional_fields_returns_the_handle() -> None:
    # Only the id is required once the sandbox exists: every other field that
    # is not a string reads as absent. The TypeScript twin is in
    # malformed-responses.test.ts.
    body: JsonObject = {
        "id": "sbx",
        "status": "pending",
        "name": 5,
        "toolbox_proxy_url": 7,
        "access_token": 8,
        "access_token_expires_at": 9,
        "image": {"id": 5, "name": 6},
        "reservation_id": 10,
        "wait_cap_seconds": "x",
    }
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = await root.v2.create("img_1", wait_seconds=0)
    assert (sandbox.id, sandbox.state, sandbox.name, sandbox.reservation_id) == (
        "sbx",
        "creating",
        None,
        None,
    )
    assert sandbox._toolbox is None
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    ("token", "expiry", "kept_token", "kept_expiry"),
    [
        (5, "2030-01-01T00:00:00Z", False, True),
        ("", "2030-01-01T00:00:00Z", False, True),
        (" padded", "2030-01-01T00:00:00Z", False, True),
        ("tok\n", "2030-01-01T00:00:00Z", False, True),
        ("tok", 5, True, False),
        ("tok", "not a date", True, False),
        ("tok", "2030-01-01T00:00:00Z", True, True),
    ],
)
async def test_a_create_token_that_cannot_be_used_is_absent(
    token: JsonValue, expiry: JsonValue, kept_token: bool, kept_expiry: bool
) -> None:
    # The sandbox exists, so the create returns the handle: a token that could
    # not be sent as a header is absent (the handle mints its own), and an
    # expiry that is not a timestamp is absent (the token is used until a 401).
    # The TypeScript twin is in malformed-responses.test.ts.
    body: JsonObject = {
        "id": "sbx",
        "status": "pending",
        "access_token": token,
        "access_token_expires_at": expiry,
    }
    with FakeApi([PlannedResponse(200, body)]) as api:
        os.environ["DAYTONA_V2_API_URL"] = api.url
        async with AsyncDaytona(DaytonaConfig(api_key="key")) as root:
            sandbox = await root.v2.create("img_1", wait_seconds=0)
    assert sandbox.access_token == (token if kept_token else None)
    assert sandbox.access_token_expires_at == (
        datetime(2030, 1, 1, tzinfo=timezone.utc) if kept_expiry else None
    )
