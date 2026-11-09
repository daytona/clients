# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

import httpx
import pytest
import respx
from typing_extensions import assert_never

from daytona.v2 import AsyncSession
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import (
    BadRequestError,
    SessionEndedError,
    SessionNotFoundError,
    UnsupportedOperationError,
)
from daytona.v2.process import AsyncProcessHandle, AsyncProcessService
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import JsonObject, JsonValue, ProcessResult

BASE = "http://toolbox.invalid"


def process_payload(kind: str = "session", **fields: JsonValue) -> JsonObject:
    payload: JsonObject = {
        "id": "shell/one",
        "kind": kind,
        "state": "running",
        "pid": 42,
        "createdAt": "2026-10-20T00:00:00Z",
        "cwd": "/app",
        "stdin": "closed",
        "keepLogs": "until_cleanup",
        "truncatedHead": False,
    }
    return payload | fields


@pytest.fixture
async def process() -> AsyncIterator[AsyncProcessService]:
    transport = AsyncTransport(BASE)
    try:
        yield AsyncProcessService("sandbox", transport, "python")
    finally:
        await transport.close()


@pytest.mark.anyio
async def test_create_session_sends_shell_settings_when_requested(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a shell creation response.
    route = respx_mock.post(f"{BASE}/processes").respond(201, json=process_payload())
    # When creating a named persistent shell.
    session = await process.create_session(
        cwd="/app",
        env={"CI": "1"},
        shell="bash",
        login=True,
        name="build",
    )
    # Then settings and process kind are sent without stdin, and the handle has an id.
    assert json.loads(route.calls.last.request.content) == {
        "kind": "session",
        "cwd": "/app",
        "env": {"CI": "1"},
        "shell": "bash",
        "login": True,
        "name": "build",
    }
    assert isinstance(session, AsyncSession)
    assert session.to_json() == {"sandboxId": "sandbox", "sessionId": "shell/one"}


@pytest.mark.anyio
async def test_run_collects_inline_output_when_session_command_exits(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given an inline terminal response with one output frame.
    route = respx_mock.post(f"{BASE}/processes").respond(
        201,
        json=process_payload(
            "exec",
            id="command",
            state="terminal",
            sessionId="shell/one",
            exitCode=0,
            reason="exited",
            logs={
                "frames": [{"cursor": "1", "channel": "stdout", "data": "ok", "timestamp": 1}],
                "nextCursor": "1",
                "eof": True,
                "truncatedHead": False,
            },
        ),
    )
    session = process.connect_session("shell/one")
    # When running with distinct caller and execution deadlines.
    result = await session.run("echo ok", timeout=2, execution_timeout=3, name="step")
    # Then the execution deadline is not dropped and output needs only one request.
    assert json.loads(route.calls.last.request.content) == {
        "kind": "exec",
        "shellCommand": "echo ok",
        "sessionId": "shell/one",
        "stdin": "closed",
        "timeoutMs": 3000,
        "name": "step",
        "keepLogs": "on_exit_ttl",
    }
    assert dict(route.calls.last.request.url.params) == {"waitMs": "2000", "logs": "text"}
    assert (result.process_id, result.stdout, result.exit_code) == ("command", "ok", 0)
    assert [frame.data for frame in result.logs] == ["ok"]
    assert route.call_count == 1


@pytest.mark.anyio
async def test_run_refuses_keep_logs_none_before_any_request(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a session, when running with a retention that would leave no log
    # to read the output from, then fail locally instead of silently keeping logs.
    session = process.connect_session("shell/one")
    with pytest.raises(BadRequestError, match="keep_logs='none'"):
        await session.run("echo ok", keep_logs="none")  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match="keep_logs='none'"):
        await process.run("echo ok", keep_logs="none")  # type: ignore[arg-type]
    assert not respx_mock.calls


@pytest.mark.anyio
async def test_start_returns_command_handle_when_session_command_is_queued(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a queued command response.
    route = respx_mock.post(f"{BASE}/processes").respond(
        201,
        json=process_payload(
            "exec",
            id="queued",
            state="starting",
            sessionId="shell/one",
        ),
    )
    # When submitting a detached command.
    handle = await process.connect_session("shell/one").start(
        "read line",
        stdin="open",
        execution_timeout=4,
        name="reader",
        keep_logs="until_cleanup",
    )
    # Then only command settings are sent and the handle can control the command.
    assert isinstance(handle, AsyncProcessHandle)
    assert (handle.id, handle.kind) == ("queued", "exec")
    assert json.loads(route.calls.last.request.content) == {
        "kind": "exec",
        "shellCommand": "read line",
        "sessionId": "shell/one",
        "stdin": "open",
        "timeoutMs": 4000,
        "name": "reader",
        "keepLogs": "until_cleanup",
    }
    assert not route.calls.last.request.url.params


@pytest.mark.anyio
async def test_get_returns_live_cwd_when_shell_directory_changes(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given updated shell metadata.
    respx_mock.get(f"{BASE}/processes/shell%2Fone").respond(200, json=process_payload(cwd="/app/src"))
    # When fetching the session.
    info = await process.connect_session("shell/one").get()
    # Then its kind is known and the live directory is exposed.
    assert (info.kind, info.cwd) == ("session", "/app/src")


@pytest.mark.anyio
@pytest.mark.parametrize("exception", [None, RuntimeError])
async def test_context_manager_deletes_session_when_scope_exits(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
    exception: type[RuntimeError] | None,
) -> None:
    # Given a bound session and deletion endpoint.
    route = respx_mock.delete(f"{BASE}/processes/shell%2Fone").respond(204)
    session = process.connect_session("shell/one")
    # When leaving a context normally or exceptionally.
    try:
        async with session as entered:
            assert entered is session
            if exception is not None:
                raise exception("command failed")
    except RuntimeError:
        assert exception is RuntimeError
    # Then deletion is performed exactly once, without follow-up reads.
    assert route.call_count == 1


@pytest.mark.anyio
async def test_context_manager_tolerates_a_session_deleted_in_the_block(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a session the block already deleted (the scope-exit DELETE 404s).
    route = respx_mock.delete(f"{BASE}/processes/shell%2Fone").mock(
        side_effect=[
            httpx.Response(204),
            httpx.Response(
                404, json={"code": "PROCESS_NOT_FOUND", "message": "gone", "source": "DAYTONA_DAEMON"}
            ),
        ]
    )
    session = process.connect_session("shell/one")
    # When / Then: no error on scope exit, which still tried its DELETE.
    async with session:
        await session.delete()
    assert route.call_count == 2


@pytest.mark.anyio
async def test_context_manager_keeps_the_blocks_error_when_cleanup_fails(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a cleanup that fails while the block raises.
    route = respx_mock.delete(f"{BASE}/processes/shell%2Fone").respond(
        500, json={"code": "INTERNAL_SERVER_ERROR", "message": "boom"}
    )
    session = process.connect_session("shell/one")
    # When / Then: the block's own error surfaces, carrying the cleanup's.
    with pytest.raises(RuntimeError, match="command failed") as raised:
        async with session:
            raise RuntimeError("command failed")
    assert route.call_count >= 1
    if sys.version_info >= (3, 11):
        assert any("cleanup also failed" in n for n in getattr(raised.value, "__notes__", []))


@pytest.mark.anyio
async def test_connect_session_restores_identity_when_serialized(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given an identity that has no transport attached.
    identity = {"sandboxId": "sandbox", "sessionId": "shell/one"}
    # When reconnecting the serialized handle.
    session = process.connect_session(process.connect_session(identity).to_json())
    # Then no metadata request was needed.
    assert (session.id, session.sandbox_id) == ("shell/one", "sandbox")
    assert session.to_json() == identity
    assert not respx_mock.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "identity",
    [
        {"sandboxId": "sandbox"},
        {"sandboxId": "sandbox", "sessionId": None},
        {"sandboxId": "sandbox", "sessionId": 7},
        {"sandboxId": "sandbox", "sessionId": ""},
        {"sessionId": "shell/one"},
    ],
)
async def test_connect_session_refuses_malformed_identity_before_any_request(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
    identity: dict[str, object],
) -> None:
    # Given a serialized identity without a usable id, when reconnecting it,
    # then fail locally: a command must never run outside its shell.
    with pytest.raises(BadRequestError):
        process.connect_session(identity)  # type: ignore[arg-type]
    assert not respx_mock.calls


@pytest.mark.anyio
async def test_connect_session_refuses_identity_when_sandbox_differs(process: AsyncProcessService) -> None:
    # Given an identity from another sandbox.
    identity = {"sandboxId": "other", "sessionId": "shell/one"}
    # When reconnecting it, then fail locally instead of binding the wrong resource.
    with pytest.raises(BadRequestError):
        process.connect_session(identity)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code,error",
    [
        (404, "SESSION_NOT_FOUND", SessionNotFoundError),
        (410, "SESSION_ENDED", SessionEndedError),
    ],
)
async def test_start_surfaces_session_loss_when_command_is_refused(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
    status: int,
    code: str,
    error: type[SessionNotFoundError | SessionEndedError],
) -> None:
    # Given a lost session.
    respx_mock.post(f"{BASE}/processes").respond(
        status,
        json={
            "code": code,
            "message": "session unavailable",
            "source": "DAYTONA_DAEMON",
        },
    )
    # When starting a command, then preserve the typed error.
    with pytest.raises(error):
        await process.connect_session("gone").start("echo ok")


@pytest.mark.anyio
@pytest.mark.parametrize("method", [AsyncProcessService.run, AsyncProcessService.start, AsyncSandbox.exec])
async def test_plain_command_refuses_session_id_when_supplied(
    method: Callable[..., Awaitable[ProcessResult | AsyncProcessHandle]],
    process: AsyncProcessService,
) -> None:
    # Given a plain-command method, when passing a session option, reject it at the call boundary.
    with pytest.raises(TypeError, match="session_id"):
        await method(process, "echo ok", session_id="shell/one")


@pytest.mark.anyio
async def test_handle_delete_removes_process_when_called(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given an ordinary process handle.
    route = respx_mock.delete(f"{BASE}/processes/command").respond(204)
    # When deleting it.
    await process.connect("command").delete()
    # Then use the shared removal endpoint exactly once.
    assert route.call_count == 1


@pytest.mark.anyio
async def test_list_filters_sessions_when_requested(
    process: AsyncProcessService,
    respx_mock: respx.MockRouter,
) -> None:
    # Given a process page containing a shell session.
    route = respx_mock.get(f"{BASE}/processes").respond(200, json={"items": [process_payload()]})
    # When filtering by session kind.
    page = await process.list(kind="session")
    # Then the query and parsed kind both retain session.
    assert route.calls.last.request.url.params["kind"] == "session"
    assert page.items[0].kind == "session"


@pytest.mark.anyio
@pytest.mark.parametrize("verb", ["resize", "write_stdin"])
async def test_session_process_handle_refuses_input_when_verb_is_not_applicable(
    process: AsyncProcessService,
    verb: Literal["resize", "write_stdin"],
) -> None:
    # Given a rehydrated session process handle.
    handle = process.connect({"sandboxId": "sandbox", "processId": "shell", "kind": "session"})
    # When attempting a PTY/input verb, then refuse it locally.
    with pytest.raises(UnsupportedOperationError):
        match verb:
            case "resize":
                await handle.resize(80, 24)
            case "write_stdin":
                await handle.write_stdin("data")
            case unreachable:
                assert_never(unreachable)
