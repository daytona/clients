# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Literal

import httpx
import pytest
import respx
from typing_extensions import assert_never

from daytona.v2 import ProcessKillIncompleteError, RequestTimeoutError
from daytona.v2._core import WAIT_READ_SLACK_SECONDS
from daytona.v2._transport import AsyncTransport
from daytona.v2.process import AsyncProcessService, AsyncPtyService
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import JsonObject, SandboxInfo

BASE = "http://toolbox.invalid"


@pytest.fixture
async def transport() -> AsyncIterator[AsyncTransport]:
    async with httpx.AsyncClient() as client:
        yield AsyncTransport(BASE, request_timeout=0.1, client=client)


@pytest.fixture
def payload() -> JsonObject:
    return {
        "id": "p_tagged",
        "kind": "exec",
        "state": "terminal",
        "cwd": "/app",
        "createdAt": "2026-11-03T00:00:00Z",
        "stdin": "closed",
        "keepLogs": "until_cleanup",
        "truncatedHead": False,
        "reason": "exited",
        "exitCode": 0,
        "logs": {"frames": [], "nextCursor": "0", "eof": True, "truncatedHead": False},
    }


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["start", "argv", "run", "session", "pty", "exec"])
@pytest.mark.parametrize("tag", [None, "job-123"])
async def test_creation_sends_tag_only_when_given(
    transport: AsyncTransport,
    payload: JsonObject,
    respx_mock: respx.MockRouter,
    operation: Literal["start", "argv", "run", "session", "pty", "exec"],
    tag: str | None,
) -> None:
    # Given a process endpoint that accepts creation.
    route = respx_mock.post(f"{BASE}/processes").respond(201, json=payload)
    process = AsyncProcessService("sandbox", transport)
    # When creating work through any public entry point.
    match operation:
        case "start":
            await process.start("echo ok", tag=tag)
        case "argv":
            await process.start(argv=["echo", "ok"], tag=tag)
        case "run":
            await process.run("echo ok", tag=tag)
        case "session":
            await process.create_session(tag=tag)
        case "pty":
            await AsyncPtyService(process).start("sh", tag=tag)
        case "exec":
            sandbox = AsyncSandbox(SandboxInfo(id="sandbox"), None, transport)
            await sandbox.exec("echo ok", tag=tag)
        case unreachable:
            assert_never(unreachable)
    # Then an absent tag stays absent for compatibility with older sandboxes.
    body = json.loads(route.calls.last.request.content)
    assert ({"tag": body["tag"]} if "tag" in body else {}) == ({} if tag is None else {"tag": tag})


@pytest.mark.anyio
@pytest.mark.parametrize("tag", [None, "job-123"])
async def test_list_filters_by_tag_when_given(
    transport: AsyncTransport,
    respx_mock: respx.MockRouter,
    tag: str | None,
) -> None:
    # Given the process listing endpoint.
    route = respx_mock.get(f"{BASE}/processes").respond(200, json={"items": []})
    # When listing tagged work or all work.
    await AsyncProcessService("sandbox", transport).list(tag=tag)
    # Then the optional filter is sent without nulls.
    assert dict(route.calls.last.request.url.params) == ({} if tag is None else {"tag": tag})


@pytest.mark.anyio
@pytest.mark.parametrize("tag", [None, "job-123"])
async def test_get_exposes_tag_when_present(
    transport: AsyncTransport,
    payload: JsonObject,
    respx_mock: respx.MockRouter,
    tag: str | None,
) -> None:
    # Given tagged or legacy metadata, with unknown fields tolerated.
    metadata = payload | {"futureField": True}
    if tag is not None:
        metadata["tag"] = tag
    respx_mock.get(f"{BASE}/processes/p_tagged").respond(200, json=metadata)
    # When reading the public process record.
    info = await AsyncProcessService("sandbox", transport).get("p_tagged")
    # Then the optional tag survives parsing.
    assert info.tag == tag


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [None, 1.234, 60.0])
@pytest.mark.parametrize("process_ids", [[], ["p_first", "p_second"]])
async def test_kill_all_waits_for_server_budget_when_called(
    transport: AsyncTransport,
    respx_mock: respx.MockRouter,
    timeout: float | None,
    process_ids: list[str],
) -> None:
    # Given a kill response and a client default shorter than the server budget.
    route = respx_mock.post(f"{BASE}/processes:kill").respond(
        200, json={"processIds": process_ids, "futureField": True}
    )
    # When killing the tag, optionally choosing a timeout in seconds.
    result = await AsyncProcessService("sandbox", transport).kill_all(tag="job-123", timeout=timeout)
    # Then the wire uses milliseconds, the read outlives the server, and ids are returned.
    expected: JsonObject = {"tag": "job-123"}
    if timeout is not None:
        expected["timeoutMs"] = int(timeout * 1000)
    request = route.calls.last.request
    assert json.loads(request.content) == expected
    assert request.extensions["timeout"]["read"] == pytest.approx(
        (10.0 if timeout is None else timeout) + WAIT_READ_SLACK_SECONDS
    )
    assert result == process_ids


@pytest.mark.anyio
async def test_kill_all_raises_typed_timeout_when_processes_survive(
    transport: AsyncTransport,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a daemon that could not prove all tagged processes stopped.
    respx_mock.post(f"{BASE}/processes:kill").respond(
        408,
        json={
            "code": "PROCESS_KILL_INCOMPLETE",
            "source": "DAYTONA_DAEMON",
            "message": "processes still running",
        },
    )
    # When killing the tag, then expose both the specific and status-level error.
    with pytest.raises(ProcessKillIncompleteError) as raised:
        await AsyncProcessService("sandbox", transport).kill_all(tag="job-123")
    assert isinstance(raised.value, RequestTimeoutError)
    assert raised.value.status_code == 408


@pytest.mark.anyio
async def test_list_keeps_only_tagged_items_when_the_daemon_ignores_the_filter(
    transport: AsyncTransport,
    payload: JsonObject,
    respx_mock: respx.MockRouter,
) -> None:
    # Given an older daemon that ignores ?tag= and returns every process.
    tagged = payload | {"id": "p_tagged", "tag": "job-1"}
    other = payload | {"id": "p_other", "tag": "job-2"}
    untagged = payload | {"id": "p_untagged"}
    respx_mock.get(f"{BASE}/processes").respond(200, json={"items": [tagged, other, untagged]})
    # When listing by tag.
    page = await AsyncProcessService("sandbox", transport).list(tag="job-1")
    # Then only the tagged process is returned.
    assert [item.id for item in page.items] == ["p_tagged"]


# Shared with the TypeScript SDK's process-tags.test.ts: a tag is a safe
# identifier ([A-Za-z0-9._-], 1-128 characters, not '.' or '..'), refused
# before any request on every entry point that takes one.
BAD_TAGS = ["", ".", "..", "a b", "a/b", "ü", "x" * 129, "tag\n", "tag\r\n", "\ntag"]
TAG_MESSAGE = "tag must be 1-128 of the characters A-Z, a-z, 0-9, '.', '_' and '-', and not '.' or '..'"


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["start", "run", "session", "pty", "exec", "list", "kill_all"])
@pytest.mark.parametrize("tag", [*BAD_TAGS, 7])
async def test_a_tag_that_is_not_a_safe_identifier_is_refused_before_any_request(
    transport: AsyncTransport,
    respx_mock: respx.MockRouter,
    operation: str,
    tag: object,
) -> None:
    from daytona.v2 import BadRequestError

    process = AsyncProcessService("sandbox", transport)
    with pytest.raises(BadRequestError, match="^tag must be 1-128") as raised:
        match operation:
            case "start":
                await process.start("echo ok", tag=tag)  # type: ignore[arg-type]
            case "run":
                await process.run("echo ok", tag=tag)  # type: ignore[arg-type]
            case "session":
                await process.create_session(tag=tag)  # type: ignore[arg-type]
            case "pty":
                await AsyncPtyService(process).start("sh", tag=tag)  # type: ignore[arg-type]
            case "exec":
                await AsyncSandbox(SandboxInfo(id="sandbox"), None, transport).exec("echo ok", tag=tag)  # type: ignore[arg-type]
            case "list":
                await process.list(tag=tag)  # type: ignore[arg-type]
            case _:
                await process.kill_all(tag=tag)  # type: ignore[arg-type]
    assert str(raised.value) == TAG_MESSAGE
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert respx_mock.calls.call_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize("tag", ["a", "x" * 128, "Job_1.2-3", "..."])
async def test_a_safe_identifier_tag_is_sent(
    transport: AsyncTransport, respx_mock: respx.MockRouter, tag: str
) -> None:
    route = respx_mock.post(f"{BASE}/processes:kill").respond(200, json={"processIds": []})
    await AsyncProcessService("sandbox", transport).kill_all(tag=tag)
    assert json.loads(route.calls.last.request.content) == {"tag": tag}


# Shared with the TypeScript SDK: the kill timeout is in seconds, more than 0
# and at most 60 (the toolbox takes 1-60000 ms); a positive one is at least
# 1 ms. Nothing above 60 means "no limit": the toolbox has no unbounded wait.
@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [0, -1, 60.0001, 61, 3_000_000, float("nan"), float("inf"), True, "5"])
async def test_a_kill_timeout_outside_0_to_60_seconds_is_refused_before_any_request(
    transport: AsyncTransport, respx_mock: respx.MockRouter, timeout: object
) -> None:
    from daytona.v2 import BadRequestError

    with pytest.raises(BadRequestError) as raised:
        await AsyncProcessService("sandbox", transport).kill_all(tag="job", timeout=timeout)  # type: ignore[arg-type]
    assert str(raised.value) == "timeout must be greater than 0 and at most 60 seconds"
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")
    assert respx_mock.calls.call_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("timeout", "milliseconds"), [(0.0001, 1), (0.0011, 2), (1, 1000), (0.1, 100), (60, 60_000)]
)
async def test_a_kill_timeout_is_sent_as_whole_milliseconds_rounded_up(
    transport: AsyncTransport, respx_mock: respx.MockRouter, timeout: float, milliseconds: int
) -> None:
    route = respx_mock.post(f"{BASE}/processes:kill").respond(200, json={"processIds": []})
    await AsyncProcessService("sandbox", transport).kill_all(tag="job", timeout=timeout)
    request = route.calls.last.request
    assert json.loads(request.content) == {"tag": "job", "timeoutMs": milliseconds}
    assert request.extensions["timeout"]["read"] == pytest.approx(
        milliseconds / 1000 + WAIT_READ_SLACK_SECONDS
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({}, "processIds"),
        ({"processIds": None}, "processIds"),
        ({"processIds": "p1"}, "processIds"),
        ({"processIds": [1]}, "processIds"),
        ([], "kill processes response"),
    ],
)
async def test_a_malformed_kill_response_is_a_bad_request_naming_the_field(
    transport: AsyncTransport, respx_mock: respx.MockRouter, body: object, message: str
) -> None:
    from daytona.v2 import BadRequestError

    respx_mock.post(f"{BASE}/processes:kill").respond(200, json=body)
    with pytest.raises(BadRequestError, match=message) as raised:
        await AsyncProcessService("sandbox", transport).kill_all(tag="job")
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_a_kill_incomplete_error_has_the_catalog_shape(
    transport: AsyncTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{BASE}/processes:kill").respond(
        408, json={"code": "PROCESS_KILL_INCOMPLETE", "source": "DAYTONA_DAEMON", "message": "still running"}
    )
    with pytest.raises(ProcessKillIncompleteError) as raised:
        await AsyncProcessService("sandbox", transport).kill_all(tag="job")
    assert (str(raised.value), raised.value.status_code, raised.value.code, raised.value.source) == (
        "still running",
        408,
        "PROCESS_KILL_INCOMPLETE",
        "DAYTONA_DAEMON",
    )


def test_process_info_with_a_tag_is_frozen() -> None:
    import dataclasses

    from daytona.v2.types import ProcessInfo

    assert ProcessInfo.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert "tag" in {field.name for field in dataclasses.fields(ProcessInfo)}


@pytest.mark.anyio
async def test_a_record_tag_that_is_not_a_string_is_a_bad_request_naming_it(
    transport: AsyncTransport, payload: JsonObject, respx_mock: respx.MockRouter
) -> None:
    from daytona.v2 import BadRequestError

    respx_mock.get(f"{BASE}/processes/p_tagged").respond(200, json=payload | {"tag": 5})
    with pytest.raises(BadRequestError, match="process response: tag") as raised:
        await AsyncProcessService("sandbox", transport).get("p_tagged")
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


# Shared with the TypeScript SDK's process-tags.test.ts: pid and exitCode are
# int32 in the contract; a value outside it is a malformed response.
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value", "ok"),
    [
        ("pid", 2**31 - 1, True),
        ("pid", 2**31, False),
        ("exitCode", -(2**31), True),
        ("exitCode", -(2**31) - 1, False),
        ("exitCode", 2**31, False),
    ],
)
async def test_process_int32_fields_are_range_checked(
    transport: AsyncTransport,
    payload: JsonObject,
    respx_mock: respx.MockRouter,
    field: str,
    value: int,
    ok: bool,
) -> None:
    from daytona.v2 import BadRequestError

    respx_mock.get(f"{BASE}/processes/p_tagged").respond(200, json=payload | {field: value})
    respx_mock.get(f"{BASE}/processes/p_tagged/wait").respond(200, json={"reason": "exited", field: value})
    process = AsyncProcessService("sandbox", transport)
    if ok:
        info = await process.get("p_tagged")
        assert (info.pid if field == "pid" else info.exit_code) == value
        if field == "exitCode":
            assert (await process.connect("p_tagged").wait()).exit_code == value
        return
    with pytest.raises(
        BadRequestError, match=f"^expected process response: {field} must be a signed 32-bit integer$"
    ):
        await process.get("p_tagged")
    if field == "exitCode":
        with pytest.raises(
            BadRequestError, match="^expected wait result: exitCode must be a signed 32-bit integer$"
        ):
            await process.connect("p_tagged").wait()
