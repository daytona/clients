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
