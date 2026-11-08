# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The toolbox namespaces' public shapes: SDK-owned models, process results and
streams, PTY options, interpreter identities and computer-use results. The
TypeScript SDK's tests/toolbox-surface.test.ts holds the same expectations."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Optional

import pytest

from daytona.v2 import (
    FileInfo,
    GitCommit,
    GitFileStatus,
    GitStatus,
    LspCompletionContext,
    LspCompletionItem,
    LspMarkupContent,
    LspPosition,
    ProcessResult,
    StreamLogEofEvent,
    StreamLogFrameEvent,
    StreamLogStateEvent,
    StreamLogWarningEvent,
    SystemMetrics,
    TerminalSize,
    WaitResult,
)
from daytona.v2._core import RequestSpec, expect_system_metrics
from daytona.v2.computer_use_types import ComputerUseDesktopFailure, ComputerUseScreenshot
from daytona.v2.errors import BadRequestError, ProcessNotFoundError
from daytona.v2.git import AsyncGitService
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.lsp import AsyncLspServer
from daytona.v2.process import AsyncProcessService, AsyncPtyService
from daytona.v2.types import JsonValue, LogFrame

CREATED_AT = "2026-01-01T00:00:00Z"


def _process(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": "proc",
        "kind": "exec",
        "state": "running",
        "cwd": "/",
        "stdin": "closed",
        "keepLogs": "on_exit_ttl",
        "createdAt": CREATED_AT,
        "truncatedHead": False,
    }
    payload.update(overrides)
    return payload


class Transport:
    """Answers each request with the next response; records the specs."""

    def __init__(self, responses: list[JsonValue] | None = None, lines: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.lines = list(lines or [])
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> JsonValue:
        self.specs.append(spec)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def stream_lines(self, spec: RequestSpec) -> AsyncIterator[str]:
        self.specs.append(spec)
        for line in self.lines:
            yield line


def test_public_models_are_frozen_keyword_only_dataclasses() -> None:
    for model in (
        WaitResult,
        ProcessResult,
        LogFrame,
        FileInfo,
        GitStatus,
        GitCommit,
        SystemMetrics,
        TerminalSize,
        LspPosition,
        LspCompletionItem,
        ComputerUseScreenshot,
        ComputerUseDesktopFailure,
    ):
        params = model.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen, model
        assert all(field.kw_only for field in dataclasses.fields(model)), model
    with pytest.raises(TypeError):
        TerminalSize(80, 24)  # type: ignore[misc]


@pytest.mark.anyio
async def test_wait_never_fabricates_an_exit_code_or_signal_and_always_has_a_reason() -> None:
    transport = Transport(
        [{"reason": "exited"}, {"reason": "signaled"}, {}, {"reason": "evicted", "exitCode": 3}]
    )
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    assert await handle.wait() == WaitResult(reason="exited", raw_reason="exited")
    assert await handle.wait() == WaitResult(reason="signaled", raw_reason="signaled")
    assert await handle.wait() == WaitResult(reason="unknown")
    assert await handle.wait() == WaitResult(reason="unknown", raw_reason="evicted", exit_code=3)


@pytest.mark.anyio
async def test_stream_logs_yields_one_tagged_event_per_server_event_and_raises_a_typed_error() -> None:
    transport = Transport(
        lines=[
            "event: log",
            'data: {"cursor":"c1","channel":"stdout","data":"hi","timestamp":43}',
            "",
            "event: state",
            'data: {"reason":"exited","exitCode":7}',
            "",
            "event: warning",
            'data: {"message":"trimmed","firstAvailableCursor":"c0"}',
            "",
            "event: future",
            "data: {}",
            "",
            "event: eof",
            'data: {"cursor":"c5"}',
            "",
            "event: error",
            'data: {"message":"gone","code":"PROCESS_NOT_FOUND","source":"DAYTONA_DAEMON"}',
            "",
        ]
    )
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]
    events: list[object] = []

    with pytest.raises(ProcessNotFoundError):
        async for event in handle.stream_logs(cursor="a", limit=5):
            events.append(event)

    assert events == [
        StreamLogFrameEvent(
            cursor="c1", frame=LogFrame(cursor="c1", channel="stdout", data="hi", timestamp_ms=43)
        ),
        StreamLogStateEvent(result=WaitResult(reason="exited", raw_reason="exited", exit_code=7)),
        StreamLogWarningEvent(message="trimmed", first_available_cursor="c0"),
        StreamLogEofEvent(cursor="c5"),
    ]
    assert transport.specs[0].params == {"encoding": "text", "cursor": "a", "limit": 5, "follow": "true"}


@pytest.mark.anyio
async def test_run_returns_the_collected_log_frames() -> None:
    transport = Transport(
        [
            {
                **_process(state="terminal", reason="exited", exitCode=0),
                "logs": {
                    "frames": [{"cursor": "1", "channel": "stdout", "data": "ok", "timestamp": 5}],
                    "nextCursor": "1",
                    "eof": True,
                    "truncatedHead": False,
                },
            }
        ]
    )
    result = await AsyncProcessService("sbx", transport).run("echo ok")  # type: ignore[arg-type]

    assert result.logs == [LogFrame(cursor="1", channel="stdout", data="ok", timestamp_ms=5)]
    assert (result.reason, result.exit_code, result.stdout) == ("exited", 0, "ok")
    assert isinstance(result, WaitResult)


@pytest.mark.anyio
async def test_process_info_reports_bare_signals_and_aware_timestamps() -> None:
    transport = Transport(
        [_process(state="terminal", reason="signaled", signal="SIGKILL", exitedAt=CREATED_AT)]
    )
    info = await AsyncProcessService("sbx", transport).get("proc")  # type: ignore[arg-type]

    assert info.signal == "KILL"
    assert info.created_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert info.exited_at == info.created_at
    assert (info.stdin, info.raw_stdin, info.keep_logs, info.cwd) == ("closed", "closed", "on_exit_ttl", "/")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal", "expected"),
    [(None, {"cols": 80, "rows": 24}), (TerminalSize(cols=120, rows=40), {"cols": 120, "rows": 40})],
)
async def test_pty_start_nests_the_terminal_size_and_defaults_to_80_by_24(
    terminal: Optional[TerminalSize], expected: dict[str, int]
) -> None:
    transport = Transport([_process(kind="pty")])
    pty = AsyncPtyService(AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    handle = await pty.start(terminal=terminal, user="dev", shell="zsh", login=True, execution_timeout=2.5)

    assert handle.kind == "pty"
    assert transport.specs[0].json_body == {
        "kind": "pty",
        "user": "dev",
        "shell": "zsh",
        "login": True,
        "stdin": "open",
        "timeoutMs": 2500,
        "keepLogs": "until_cleanup",
        "terminal": expected,
    }


@pytest.mark.anyio
async def test_exec_and_run_have_no_terminal_option() -> None:
    process = AsyncProcessService("sbx", Transport())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        await process.run("true", terminal=TerminalSize(cols=1, rows=1))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        await process.start("true", terminal=TerminalSize(cols=1, rows=1))  # type: ignore[call-arg]


@pytest.mark.anyio
async def test_interpreter_run_by_context_id_needs_the_language() -> None:
    transport = Transport()
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="language is required"):
        await interpreter.run("1", context="ctx")
    with pytest.raises(BadRequestError, match="does not match"):
        await interpreter.run(
            "1", context={"sandboxId": "sbx", "contextId": "ctx", "language": "python"}, language="r"
        )
    assert transport.specs == []
    assert not hasattr(interpreter, "get_context")


def test_interpreter_context_identity_round_trips_in_camel_case() -> None:
    transport = Transport()
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context = interpreter.connect_context("ctx", language="python")

    assert context.to_json() == {"sandboxId": "sbx", "contextId": "ctx", "language": "python"}
    assert interpreter.connect_context(context.to_json()) == context
    with pytest.raises(BadRequestError):
        interpreter.connect_context({"sandboxId": "other", "contextId": "ctx", "language": "python"})


@pytest.mark.anyio
async def test_git_results_are_sdk_models() -> None:
    transport = Transport(
        [
            {
                "currentBranch": "main",
                "ahead": 1,
                "behind": 0,
                "branchPublished": True,
                "fileStatus": [{"name": "README.md", "staging": "M", "worktree": " ", "extra": ""}],
            },
            [
                {
                    "hash": "h",
                    "message": "m",
                    "author": "A",
                    "email": "a@example.invalid",
                    "timestamp": CREATED_AT,
                }
            ],
            {"currentBranch": "main"},
        ]
    )
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    assert await git.status("/repo") == GitStatus(
        current_branch="main",
        ahead=1,
        behind=0,
        branch_published=True,
        file_status=[GitFileStatus(name="README.md", staging="M", worktree=" ", extra="")],
    )
    assert (await git.history("/repo")).items == [
        GitCommit(
            hash="h",
            message="m",
            author="A",
            email="a@example.invalid",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    ]
    with pytest.raises(BadRequestError, match="git status response"):
        await git.status("/repo")


@pytest.mark.anyio
async def test_lsp_completions_return_sdk_models_and_send_a_typed_context() -> None:
    transport = Transport(
        [
            {
                "isIncomplete": False,
                "items": [
                    {"label": "plain", "documentation": "text", "kind": 3},
                    {
                        "label": "rich",
                        "documentation": {"kind": "markdown", "value": "**x**"},
                        "sortText": "a",
                    },
                ],
            }
        ]
    )
    server = AsyncLspServer(transport, "python", "/p")  # type: ignore[arg-type]

    completions = await server.completions(
        "file:///p/a.py",
        LspPosition(line=1, character=2),
        context=LspCompletionContext(trigger_kind=2, trigger_character="."),
    )

    assert completions.items == [
        LspCompletionItem(label="plain", kind=3, documentation="text"),
        LspCompletionItem(
            label="rich", documentation=LspMarkupContent(kind="markdown", value="**x**"), sort_text="a"
        ),
    ]
    assert transport.specs[0].json_body == {
        "languageId": "python",
        "pathToProject": "/p",
        "uri": "file:///p/a.py",
        "position": {"line": 1, "character": 2},
        "context": {"triggerKind": 2, "triggerCharacter": "."},
    }


def test_system_metrics_is_an_sdk_model_with_an_aware_timestamp() -> None:
    metrics = expect_system_metrics(
        {
            "timestamp": "2026-01-01T00:00:00.5+02:00",
            "cpuCount": 2,
            "cpuUtilization": 0.25,
            "memoryUsageBytes": 1,
            "memoryTotalBytes": 2,
            "diskUsageBytes": 3,
            "diskTotalBytes": 4,
        }
    )
    assert metrics.timestamp.utcoffset() is not None
    assert (metrics.cpu_count, metrics.disk_total_bytes) == (2, 4)
    with pytest.raises(BadRequestError):
        expect_system_metrics({"cpuCount": 2})


def test_computer_use_results_require_the_contract_fields() -> None:
    with pytest.raises(TypeError):
        ComputerUseScreenshot(screenshot="x")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        ComputerUseDesktopFailure(code="unknown", message="m")  # type: ignore[call-arg]
