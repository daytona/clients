# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Process, interpreter and git arguments are refused before any request,
with messages that name the called method's own parameters."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import pytest

from daytona.v2._core import RequestSpec
from daytona.v2.errors import BadRequestError, UnsupportedOperationError
from daytona.v2.git import AsyncGitService
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.process import AsyncProcessService, AsyncPtyService
from daytona.v2.session import AsyncSession
from daytona.v2.types import JsonValue


class Transport:
    """Answers each request with the next response; records the specs."""

    def __init__(self, responses: list[JsonValue] | None = None) -> None:
        self.responses = list(responses or [])
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> JsonValue:
        self.specs.append(spec)
        return self.responses.pop(0)


def _services(transport: Transport) -> tuple[AsyncProcessService, AsyncInterpreterService]:
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]
    return process, AsyncInterpreterService("sbx", transport, process)  # type: ignore[arg-type]


async def _refused(call: Callable[[], Awaitable[object]], message: str, transport: Transport) -> None:
    with pytest.raises(BadRequestError) as caught:
        await call()
    assert str(caught.value) == message
    assert caught.value.source == "SDK"
    assert transport.specs == []


Case = Callable[[AsyncProcessService, AsyncInterpreterService], Awaitable[object]]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda p, i: AsyncPtyService(p).start(123), "command must be a string"),
        (lambda p, i: p.start(123), "command must be a string"),
        (lambda p, i: p.run(b"ls"), "command must be a string"),
        (lambda p, i: p.run(), "provide exactly one of command or argv"),
        (lambda p, i: p.run("ls", argv=["ls"]), "provide exactly one of command or argv"),
        (lambda p, i: p.start(), "provide exactly one of command or argv"),
        (lambda p, i: AsyncSession("sbx", "s", p._transport).run(None), "command must be a string"),
        (lambda p, i: AsyncSession("sbx", "s", p._transport).start(5), "command must be a string"),
        (lambda p, i: i.run(123), "code must be a string"),
        (lambda p, i: i.run(None), "code must be a string"),
        (lambda p, i: i.run("1", language=""), "language must be a non-empty string"),
        (lambda p, i: i.run("1", language=5), "language must be a non-empty string"),
        (lambda p, i: i.run("1", language="", isolated=True), "language must be a non-empty string"),
        (lambda p, i: i.create_context(language=""), "language must be a non-empty string"),
        (
            lambda p, i: i.connect_context("ctx", language="python").run(5),
            "code must be a string",
        ),
    ],
)
async def test_process_and_interpreter_inputs_are_refused_locally(call: Case, message: str) -> None:
    transport = Transport()
    process, interpreter = _services(transport)
    await _refused(lambda: call(process, interpreter), message, transport)


@pytest.mark.anyio
async def test_commandless_pty_still_starts_the_default_shell() -> None:
    transport = Transport(
        [
            {
                "id": "pty-1",
                "kind": "pty",
                "state": "running",
                "cwd": "/",
                "stdin": "open",
                "keepLogs": "until_cleanup",
                "createdAt": "2026-01-01T00:00:00Z",
                "truncatedHead": False,
            }
        ]
    )
    process, _ = _services(transport)
    handle = await AsyncPtyService(process).start()
    assert handle.kind == "pty"
    assert transport.specs[0].json_body is not None
    assert "shellCommand" not in transport.specs[0].json_body


def test_connect_context_refuses_a_conflicting_language_like_run() -> None:
    transport = Transport()
    _, interpreter = _services(transport)
    identity = {"sandboxId": "sbx", "contextId": "ctx", "language": "python"}
    with pytest.raises(
        BadRequestError, match="language 'javascript' does not match the context's language 'python'"
    ):
        interpreter.connect_context(identity, language="javascript")
    handle = interpreter.connect_context(identity)
    with pytest.raises(BadRequestError, match="does not match the context's language"):
        interpreter.connect_context(handle, language="javascript")
    assert interpreter.connect_context(identity, language="python").language == "python"
    with pytest.raises(BadRequestError, match="interpreter context language must be a non-empty string"):
        interpreter.connect_context(identity, language="")
    with pytest.raises(BadRequestError, match="interpreter context id must be a non-empty string"):
        interpreter.connect_context(None, language="python")  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"signal": ""}, "signal must be one of TERM, KILL, INT, HUP, QUIT, USR1, USR2"),
        ({"signal": "SIGTERM"}, "signal must be one of"),
        ({"signal": "STOP"}, "signal must be one of"),
        ({"signal": "k\u0131ll"}, "signal must be one of"),
        ({"signal": None}, "signal must be one of"),
        ({"escalate_to": "KILL"}, "escalate_to needs escalate_after"),
        ({"escalate_after": 1, "escalate_to": "SIGKILL"}, "escalate_to must be one of"),
        ({"escalate_after": 601}, "escalate_after must be at most 600 seconds"),
        ({"escalate_after": 600.0001}, "escalate_after must be at most 600 seconds"),
        ({"escalate_after": 1e300}, "escalate_after must be at most 600 seconds"),
        ({"escalate_after": -1}, "escalate_after must be a non-negative finite number"),
    ],
)
async def test_kill_refuses_invalid_signals_before_any_request(kwargs: dict[str, Any], message: str) -> None:
    transport = Transport()
    process, _ = _services(transport)
    with pytest.raises(BadRequestError, match=message):
        await process.connect("p").kill(**kwargs)
    assert transport.specs == []


@pytest.mark.anyio
async def test_kill_normalizes_signal_case_and_accepts_the_longest_delay() -> None:
    transport = Transport([None, None])
    process, _ = _services(transport)
    await process.connect("p").kill(signal="int", escalate_after=600, escalate_to="kill")  # type: ignore[arg-type]
    await process.connect("p").kill(signal="term")  # type: ignore[arg-type]
    assert [spec.json_body for spec in transport.specs] == [
        {"signal": "SIGINT", "escalateAfterMs": 600000, "escalateTo": "SIGKILL"},
        {"signal": "SIGTERM"},
    ]


@pytest.mark.anyio
async def test_pty_delete_names_itself_when_refusing_another_kind() -> None:
    transport = Transport(
        [
            {
                "id": "p",
                "kind": "exec",
                "state": "running",
                "cwd": "/",
                "stdin": "closed",
                "keepLogs": "on_exit_ttl",
                "createdAt": "2026-01-01T00:00:00Z",
                "truncatedHead": False,
            }
        ]
    )
    process, _ = _services(transport)
    with pytest.raises(
        UnsupportedOperationError, match=r"^pty\.delete\(\) is only supported for pty handles"
    ):
        await AsyncPtyService(process).delete("p")
    assert [spec.method for spec in transport.specs] == ["GET"]


GitCall = Callable[[AsyncGitService], Awaitable[object]]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda g: g.commit("/r", "m", author="a", email="e", allow_empty="true"),
            "allow_empty must be a boolean",
        ),
        (lambda g: g.init("/r", bare="yes"), "bare must be a boolean"),
        (lambda g: g.push("/r", set_upstream="yes"), "set_upstream must be a boolean"),
        (lambda g: g.push("/r", set_upstream=1), "set_upstream must be a boolean"),
        (lambda g: g.restore("/r", ["f"], staged="false"), "staged must be a boolean"),
        (lambda g: g.restore("/r", ["f"], worktree=0), "worktree must be a boolean"),
        (lambda g: g.clone("u", "/r", insecure_skip_tls="true"), "insecure_skip_tls must be a boolean"),
        (lambda g: g.delete_branch("/r", "b", force="true"), "force must be a boolean"),
    ],
)
async def test_git_booleans_are_checked_before_any_request(call: GitCall, message: str) -> None:
    transport = Transport()
    await _refused(lambda: call(AsyncGitService(transport)), message, transport)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_git_booleans_reach_the_wire() -> None:
    transport = Transport([{"hash": "abc"}, None, None])
    git = AsyncGitService(transport)  # type: ignore[arg-type]
    await git.commit("/r", "m", author="a", email="e", allow_empty=True)
    await git.init("/r", bare=True)
    await git.push("/r", set_upstream=True)
    assert transport.specs[0].json_body == {
        "path": "/r",
        "message": "m",
        "author": "a",
        "email": "e",
        "allow_empty": True,
    }
    assert transport.specs[1].json_body == {"path": "/r", "bare": True}
    assert transport.specs[2].json_body == {"path": "/r", "setUpstream": True}
