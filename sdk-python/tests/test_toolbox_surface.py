# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The toolbox namespaces' public shapes: SDK-owned models, process results and
streams, PTY options, interpreter identities and computer-use results. The
TypeScript SDK's `toolbox surface` block in tests/unit.test.ts holds the same expectations."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Optional

import pytest

from daytona.v2 import (
    AsyncWatchHandle,
    BulkWriteFile,
    BulkWriteResult,
    FileInfo,
    FilesystemEvent,
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
from daytona.v2.errors import BadRequestError, ConnectionError, ProcessNotFoundError
from daytona.v2.filesystem import AsyncFileSystemService
from daytona.v2.git import AsyncGitService
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.lsp import AsyncLspServer
from daytona.v2.process import AsyncProcessService, AsyncPtyService
from daytona.v2.types import JsonValue, LogFrame, LogPage

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
            "id: c2",
            "event: state",
            'data: {"id":"proc","kind":"exec","state":"terminal","cwd":"/","stdin":"closed","keepLogs":"on_exit_ttl","createdAt":"2026-01-01T00:00:00Z","truncatedHead":false,"reason":"exited","exitCode":7}',
            "",
            "event: warning",
            'data: {"cursor":"c3","message":"trimmed","firstAvailableCursor":"c0"}',
            "",
            "event: future",
            "data: {}",
            "",
            "event: error",
            'data: {"cursor":"c6","message":"gone","code":"PROCESS_NOT_FOUND","source":"DAYTONA_DAEMON"}',
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
        StreamLogStateEvent(
            cursor="c2", result=WaitResult(reason="exited", raw_reason="exited", exit_code=7)
        ),
        StreamLogWarningEvent(cursor="c3", message="trimmed", first_available_cursor="c0"),
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


class _WatchStream:
    def __init__(self, lines: list[str]) -> None:
        self._queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        for line in lines:
            self._queue.put_nowait(line)
        self.closed = False

    def push(self, *lines: str) -> None:
        for line in lines:
            self._queue.put_nowait(line)

    async def iter_lines(self) -> AsyncIterator[str]:
        while True:
            line = await self._queue.get()
            if line is None:
                return
            yield line

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(None)


class _WatchTransport:
    def __init__(self, stream: _WatchStream) -> None:
        self.stream = stream

    async def open_stream(self, spec: RequestSpec) -> _WatchStream:
        return self.stream


_FS_A = ("event: fs", 'data: {"type":"create","name":"a.txt","isDir":false,"timestamp":1}', "")


@pytest.mark.anyio
async def test_a_callback_can_stop_its_watch_and_then_wait_for_its_exit() -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    holder: list[AsyncWatchHandle] = []
    results: list[object] = []

    async def on_event(_event: FilesystemEvent) -> None:
        await holder[0].stop()
        results.append(await holder[0].wait_for_exit())

    holder.append(await fs.watch_dir("/w", on_event))
    stream.push(*_FS_A)

    assert await asyncio.wait_for(holder[0].wait_for_exit(), timeout=2) is None
    assert results == [None]


@pytest.mark.anyio
async def test_on_exit_can_stop_and_wait_for_its_own_watch_and_later_stops_return() -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    holder: list[AsyncWatchHandle] = []
    done = asyncio.Event()

    async def on_exit(_error: Optional[Exception]) -> None:
        await holder[0].stop()
        await holder[0].wait_for_exit()
        done.set()

    holder.append(await fs.watch_dir("/w", lambda _event: None, on_exit=on_exit))
    await asyncio.wait_for(holder[0].stop(), timeout=2)
    await asyncio.wait_for(done.wait(), timeout=2)
    await asyncio.wait_for(holder[0].stop(), timeout=2)


@pytest.mark.anyio
async def test_malformed_watch_events_end_the_watch_with_a_bad_request_error() -> None:
    stream = _WatchStream(
        ["event: start", "data: {}", "", "event: fs", 'data: {"type":"create","name":"a"}', ""]
    )
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    handle = await fs.watch_dir("/w", lambda _event: None)

    error = await asyncio.wait_for(handle.wait_for_exit(), timeout=2)
    assert isinstance(error, BadRequestError)
    assert (error.status_code, error.code, error.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("lines", "field"),
    [
        (["event: eof", "data: {}", ""], "cursor"),
        (["event: warning", 'data: {"cursor":"c","firstAvailableCursor":"c0"}', ""], "message"),
        (["event: warning", 'data: {"cursor":"c","message":"m"}', ""], "firstAvailableCursor"),
        (["event: error", 'data: {"cursor":"c"}', ""], "message"),
        (["event: log", 'data: {"channel":"stdout","data":"x","timestamp":1}', ""], "cursor"),
    ],
)
async def test_stream_logs_rejects_an_event_missing_a_required_field(lines: list[str], field: str) -> None:
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match=field) as info:
        async for _event in handle.stream_logs():
            pass
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_stream_logs_takes_an_event_cursor_from_the_sse_id_line() -> None:
    lines = [
        "id: c1",
        "event: log",
        'data: {"channel":"stdout","data":"x","timestamp":1}',
        "",
        "id: c2",
        "event: eof",
        "data: {}",
        "",
    ]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    events = [event async for event in handle.stream_logs()]

    assert events == [
        StreamLogFrameEvent(
            cursor="c1", frame=LogFrame(cursor="c1", channel="stdout", data="x", timestamp_ms=1)
        ),
        StreamLogEofEvent(cursor="c2"),
    ]


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [(1e-9, 1), (0.0004, 1), (0.0015, 2), (0, 0), (2_147_483.647, 2_147_483_647)],
)
def test_positive_sub_millisecond_durations_are_at_least_one_millisecond(
    timeout: float, expected: int
) -> None:
    from daytona.v2._core import timeout_millis_from_seconds

    assert timeout_millis_from_seconds(timeout) == expected


@pytest.mark.anyio
async def test_a_wait_budget_above_the_timer_maximum_is_no_limit() -> None:
    transport = Transport([{"reason": "exited", "exitCode": 0}])
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    await handle.wait(timeout=10**9)

    assert transport.specs[0].params == {"timeoutMs": 0}


@pytest.mark.anyio
async def test_list_remotes_rejects_a_response_without_remotes() -> None:
    for payload in ({}, {"remotes": None}, {"remotes": "origin"}):
        with pytest.raises(BadRequestError, match="remotes is required"):
            await AsyncGitService(Transport([payload])).list_remotes("/repo")  # type: ignore[arg-type]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "context",
    [
        LspCompletionContext(trigger_kind="2"),  # type: ignore[arg-type]
        LspCompletionContext(trigger_kind=True),
        LspCompletionContext(trigger_kind=2, trigger_character=1),  # type: ignore[arg-type]
    ],
)
async def test_lsp_completion_context_fields_are_type_checked(context: LspCompletionContext) -> None:
    transport = Transport()
    server = AsyncLspServer(transport, "python", "/p")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError):
        await server.completions("file:///a.py", LspPosition(line=0, character=0), context=context)
    assert transport.specs == []


# The same malformed computer-use responses as the TypeScript SDK's
# "rejects every malformed computer-use result" test.
_MALFORMED_COMPUTER_USE = [
    ("screenshot", {"screenshot": "AAAA"}),
    (
        "processStatusMap",
        {
            "wm": {
                "Priority": 1,
                "AutoRestart": True,
                "Failed": False,
                "RestartAttempts": 0,
                "RestartBackoffMs": 0,
                "Status": "running",
            }
        },
    ),
    (
        "processStatusMap",
        {
            "wm": {
                "Running": True,
                "AutoRestart": True,
                "Failed": False,
                "RestartAttempts": 0,
                "RestartBackoffMs": 0,
                "Status": "running",
            }
        },
    ),
    (
        "processStatusMap",
        {
            "wm": {
                "Running": True,
                "Priority": 1,
                "Failed": False,
                "RestartAttempts": 0,
                "RestartBackoffMs": 0,
                "Status": "running",
            }
        },
    ),
    (
        "processStatusMap",
        {
            "wm": {
                "Running": True,
                "Priority": 1,
                "AutoRestart": True,
                "RestartAttempts": 0,
                "RestartBackoffMs": 0,
                "Status": "running",
            }
        },
    ),
    (
        "processStatusMap",
        {
            "wm": {
                "Running": True,
                "Priority": 1,
                "AutoRestart": True,
                "Failed": False,
                "RestartBackoffMs": 0,
                "Status": "running",
            }
        },
    ),
    (
        "processStatusMap",
        {
            "wm": {
                "Running": True,
                "Priority": 1,
                "AutoRestart": True,
                "Failed": False,
                "RestartAttempts": 0,
                "Status": "running",
            }
        },
    ),
    (
        "processStatusMap",
        {
            "wm": {
                "Running": True,
                "Priority": 1,
                "AutoRestart": True,
                "Failed": False,
                "RestartAttempts": 0,
                "RestartBackoffMs": 0,
            }
        },
    ),
    ("lifecycleStart", {"message": "ok"}),
    ("displayInfo", {"displays": [{"id": 0, "x": 0, "y": 0, "width": 1, "height": 1}]}),
    ("displayWindows", {"windows": [{"id": 1, "x": 0, "y": 0, "width": 1, "height": 1, "isActive": True}]}),
    ("a11yTree", {"truncated": False}),
    ("a11yTree", {"root": {"id": "n", "role": "window", "name": "w", "states": []}, "truncated": False}),
    (
        "a11yTree",
        {
            "root": {
                "id": "n",
                "role": "window",
                "name": "w",
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
            },
            "truncated": False,
        },
    ),
    ("a11yFind", {"truncated": False}),
    ("recordingGet", {"id": "r", "fileName": "a.mp4", "filePath": "/a.mp4", "status": "recording"}),
    ("recordingList", {}),
    ("mousePosition", {"x": 1}),
    ("scroll", {}),
    ("processRunning", {"processName": "wm"}),
    ("processRestart", {"message": "ok"}),
    ("processLogs", {"processName": "wm"}),
    ("processErrors", {"processName": "wm"}),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("operation", "payload"), _MALFORMED_COMPUTER_USE)
async def test_every_malformed_computer_use_result_is_the_malformed_response_error(
    operation: str, payload: dict[str, object]
) -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    cu = AsyncComputerUseService(Transport([payload]))  # type: ignore[arg-type]
    calls = {
        "screenshot": lambda: cu.screen.screenshot(),
        "processStatusMap": lambda: cu.service.status(),
        "lifecycleStart": lambda: cu.start(),
        "displayInfo": lambda: cu.display.info(),
        "displayWindows": lambda: cu.display.windows(),
        "a11yTree": lambda: cu.a11y.tree(scope="focused"),
        "a11yFind": lambda: cu.a11y.find(scope="all"),
        "recordingGet": lambda: cu.recording.get("r"),
        "recordingList": lambda: cu.recording.list(),
        "mousePosition": lambda: cu.mouse.position(),
        "scroll": lambda: cu.mouse.scroll(x=0, y=0, direction="down"),
        "processRunning": lambda: cu.service.status("wm"),
        "processRestart": lambda: cu.service.restart("wm"),
        "processLogs": lambda: cu.service.logs("wm"),
        "processErrors": lambda: cu.service.errors("wm"),
    }
    with pytest.raises(BadRequestError) as info:
        await calls[operation]()
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_mkdir_defaults_to_mode_0755_like_the_typescript_sdk() -> None:
    transport = Transport([None])
    await AsyncFileSystemService(transport).mkdir("/w")  # type: ignore[arg-type]
    assert transport.specs[0].params == {"path": "/w", "mode": "0755"}


@pytest.mark.anyio
async def test_stream_logs_skips_an_unknown_event_with_non_json_data() -> None:
    lines = [
        "event: ping",
        "data: not json",
        "",
        "event: log",
        'data: {"cursor":"c1","channel":"stdout","data":"x","timestamp":1}',
        "",
        "event: eof",
        'data: {"cursor":"c2"}',
        "",
    ]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    assert [event.type async for event in handle.stream_logs()] == ["log", "eof"]


@pytest.mark.anyio
async def test_stream_logs_raises_the_malformed_response_error_for_a_known_event_with_non_json_data() -> None:
    lines = ["event: state", "data: not json", ""]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="process log state event: the data is not JSON") as info:
        async for _event in handle.stream_logs():
            pass
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_a_watch_skips_unknown_events_with_non_json_data_and_rejects_a_bad_fs_event() -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    seen: list[str] = []
    handle = await fs.watch_dir("/w", lambda event: seen.append(event.name))
    stream.push("event: ping", "data: not json", "", "event: overflow", "data: nope", "", *_FS_A)
    stream.push("event: fs", "data: not json", "")

    error = await asyncio.wait_for(handle.wait_for_exit(), timeout=2)
    assert seen == ["a.txt"]
    assert isinstance(error, BadRequestError)
    assert "filesystem watch event: the data is not JSON" in str(error)


@pytest.mark.anyio
async def test_a_watch_delivers_an_unknown_event_kind_as_its_string() -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    seen: list[FilesystemEvent] = []
    done = asyncio.Event()

    def on_event(event: FilesystemEvent) -> None:
        seen.append(event)
        done.set()

    handle = await fs.watch_dir("/w", on_event)
    stream.push("event: fs", 'data: {"type":"attrib","name":"a","isDir":false,"timestamp":1}', "")
    await asyncio.wait_for(done.wait(), timeout=2)

    assert seen == [FilesystemEvent(type="attrib", name="a", is_dir=False, timestamp_ms=1)]
    assert handle.exit_error is None
    await handle.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["create_task", "gather", "wait_for"])
async def test_a_callback_stopping_its_watch_from_a_child_task_does_not_deadlock(how: str) -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    holder: list[AsyncWatchHandle] = []

    async def on_event(_event: FilesystemEvent) -> None:
        handle = holder[0]
        if how == "create_task":
            await asyncio.create_task(handle.stop())
        elif how == "gather":
            await asyncio.gather(handle.stop(), handle.wait_for_exit())
        else:
            await asyncio.wait_for(handle.stop(), timeout=5)

    holder.append(await fs.watch_dir("/w", on_event))
    stream.push(*_FS_A)

    assert await asyncio.wait_for(holder[0].wait_for_exit(), timeout=2) is None


@pytest.mark.anyio
async def test_an_external_stop_returns_after_on_exit_has_finished() -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    order: list[str] = []

    async def on_exit(_error: Optional[Exception]) -> None:
        await asyncio.sleep(0.05)
        order.append("on_exit finished")

    handle = await fs.watch_dir("/w", lambda _event: None, on_exit=on_exit)
    await asyncio.wait_for(handle.stop(), timeout=2)
    order.append("stop returned")

    assert order == ["on_exit finished", "stop returned"]


@pytest.mark.anyio
async def test_a_stop_a_callback_defers_past_its_delivery_waits_like_an_external_one() -> None:
    stream = _WatchStream(["event: start", "data: {}", ""])
    fs = AsyncFileSystemService(_WatchTransport(stream))  # type: ignore[arg-type]
    holder: list[AsyncWatchHandle] = []
    deferred: list[asyncio.Task[None]] = []
    release_b = asyncio.Event()
    b_finished = asyncio.Event()

    async def on_event(event: FilesystemEvent) -> None:
        if event.name == "a.txt":

            async def later() -> None:
                await asyncio.sleep(0.01)
                await holder[0].stop()

            deferred.append(asyncio.create_task(later()))
            return
        await release_b.wait()
        b_finished.set()

    holder.append(await fs.watch_dir("/w", on_event))
    stream.push(*_FS_A, "event: fs", 'data: {"type":"create","name":"b.txt","isDir":false,"timestamp":2}', "")
    await asyncio.sleep(0.1)

    # The task outlived a.txt's delivery: its stop() waits for b.txt's callback.
    assert deferred and not deferred[0].done()
    release_b.set()
    await asyncio.wait_for(deferred[0], timeout=2)
    assert b_finished.is_set()


_LOG_PAGE = {"frames": [], "nextCursor": "c0", "truncatedHead": False, "eof": True}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "eof"),
        ({**_LOG_PAGE, "frames": "x"}, "frames must be a list"),
        ({**_LOG_PAGE, "truncatedHead": "yes"}, "truncatedHead"),
        ({key: value for key, value in _LOG_PAGE.items() if key != "nextCursor"}, "nextCursor"),
        ({key: value for key, value in _LOG_PAGE.items() if key != "eof"}, "eof"),
        ({**_LOG_PAGE, "firstAvailableCursor": 5}, "firstAvailableCursor"),
        ([], "process log page"),
    ],
)
async def test_logs_rejects_a_page_that_breaks_the_contract(payload: JsonValue, field: str) -> None:
    handle = AsyncProcessService("sbx", Transport([payload])).connect("proc")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match=field) as info:
        await handle.logs()
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_logs_returns_the_contract_page() -> None:
    frame = {"cursor": "c1", "channel": "stdout", "data": "x", "timestamp": 1}
    payload = {
        "frames": [frame],
        "nextCursor": "c1",
        "truncatedHead": True,
        "eof": False,
        "firstAvailableCursor": "c0",
    }
    handle = AsyncProcessService("sbx", Transport([payload])).connect("proc")  # type: ignore[arg-type]

    page = await handle.logs()

    assert page == LogPage(
        items=[LogFrame(cursor="c1", channel="stdout", data="x", timestamp_ms=1)],
        next_cursor="c1",
        truncated_head=True,
        eof=False,
        first_available_cursor="c0",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "errors"),
        ({"files": [], "errors": None}, "errors"),
        ({"files": "x", "errors": []}, "files must be a list"),
        ({"files": [{}], "errors": []}, "bytes"),
        ({"files": [{"path": "/a", "bytes": "x"}], "errors": []}, "bytes"),
        ({"files": [{"path": "/a", "bytes": -1}], "errors": []}, "bytes"),
    ],
)
async def test_write_many_rejects_a_bulk_upload_response_that_breaks_the_contract(
    payload: JsonValue, field: str
) -> None:
    fs = AsyncFileSystemService(Transport([payload]))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match=f"bulk upload response: {field}") as info:
        await fs.write_many({"/a": b"x"})
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_write_many_returns_sdk_models() -> None:
    fs = AsyncFileSystemService(
        Transport([{"files": [{"path": "/a", "bytes": 1, "extra": 1}], "errors": ["b"]}])
    )  # type: ignore[arg-type]

    assert await fs.write_many({"/a": b"x"}) == BulkWriteResult(
        files=[BulkWriteFile(path="/a", bytes=1)], errors=["b"]
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        ("grep", {"items": [{"file": "/a", "line": -1, "content": "x"}]}, "grep match: line"),
        ("grep", {"items": [{"file": "/a", "line": "3", "content": "x"}]}, "grep match: line"),
        ("replace", {"items": [{"file": "/a", "success": "yes"}]}, "replace result: success"),
        ("list", {"items": [{"name": "a"}]}, "file info response"),
    ],
)
async def test_malformed_filesystem_results_name_the_field(
    operation: str, payload: JsonValue, message: str
) -> None:
    fs = AsyncFileSystemService(Transport([payload]))  # type: ignore[arg-type]
    calls = {
        "grep": lambda: fs.grep("/", "x"),
        "replace": lambda: fs.replace(["/a"], "x", "y"),
        "list": lambda: fs.list("/"),
    }

    with pytest.raises(BadRequestError, match=message):
        await calls[operation]()


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01T00:00:00Z\n",
        " 2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00+24:00",
        "2026-01-01T00:00:00+05:60",
        "2026-12-31T23:59:60Z",
        "2026-13-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-01-01T00:00:00",
        "Jan 1 2026",
        "0000-01-01T00:00:00Z",
    ],
)
def test_timestamps_reject_what_is_not_rfc_3339_like_the_typescript_sdk(value: str) -> None:
    from daytona.v2._timestamps import parse_timestamp

    with pytest.raises(BadRequestError, match="field: not an RFC 3339 timestamp"):
        parse_timestamp(value, "field")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-01T00:00:00.123456789Z", datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=timezone.utc)),
        ("0001-01-01t00:00:00z", datetime(1, 1, 1, tzinfo=timezone.utc)),
        ("0099-06-01 12:00:00+00:00", datetime(99, 6, 1, 12, tzinfo=timezone.utc)),
    ],
)
def test_timestamps_accept_rfc_3339_variants_like_the_typescript_sdk(value: str, expected: datetime) -> None:
    from daytona.v2._timestamps import parse_timestamp

    assert parse_timestamp(value, "field") == expected


@pytest.mark.anyio
async def test_run_without_a_timeout_holds_one_60_second_window_like_the_typescript_sdk() -> None:
    terminal = _process(state="terminal", reason="exited", exitCode=0)
    terminal["logs"] = {"frames": [], "nextCursor": "0", "truncatedHead": False, "eof": True}
    transport = Transport([terminal, terminal])
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    await process.run("true")
    await process.run("true", timeout=0)

    assert transport.specs[0].params == {"waitMs": 60000, "logs": "text"}
    assert transport.specs[0].timeout == 65.0
    assert transport.specs[1].params == {"waitMs": 0, "logs": "text"}
    assert transport.specs[1].unbounded_read


def _toolbox_classes() -> list[type]:
    from daytona.v2 import (
        computer_use,
        computer_use_services,
        filesystem,
        git,
        interpreter,
        lsp,
        process,
        session,
    )
    from daytona.v2 import types as types_module

    return [
        process.AsyncProcessService,
        process.AsyncProcessHandle,
        process.AsyncPtyService,
        session.AsyncSession,
        interpreter.AsyncInterpreterService,
        types_module.AsyncInterpreterContext,
        filesystem.AsyncFileSystemService,
        filesystem.AsyncWatchHandle,
        git.AsyncGitService,
        lsp.AsyncLspService,
        lsp.AsyncLspServer,
        computer_use.AsyncComputerUseService,
        computer_use_services.AsyncAddonProcessesService,
        computer_use_services.AsyncMouseService,
        computer_use_services.AsyncKeyboardService,
        computer_use_services.AsyncScreenService,
        computer_use_services.AsyncDisplayService,
        computer_use_services.AsyncA11yService,
        computer_use_services.AsyncRecordingService,
    ]


@pytest.mark.parametrize("cls", _toolbox_classes(), ids=lambda cls: cls.__name__)
def test_toolbox_constructors_take_only_private_parameters(cls: type) -> None:
    import inspect

    parameters = [name for name in inspect.signature(cls.__init__).parameters if name != "self"]
    assert parameters and all(name.startswith("_") for name in parameters), parameters
    public = parameters[0].lstrip("_")
    with pytest.raises(TypeError, match=f"unexpected keyword argument '{public}'"):
        cls(**{public: None})


@pytest.mark.anyio
async def test_read_text_keeps_a_byte_order_mark_and_decodes_strictly_like_the_typescript_sdk() -> None:
    class BytesTransport:
        def __init__(self) -> None:
            self.bodies = [b"\xef\xbb\xbfhi", b"\xff"]

        async def raw(self, spec: RequestSpec) -> bytes:
            return self.bodies.pop(0)

    fs = AsyncFileSystemService(BytesTransport())  # type: ignore[arg-type]

    assert await fs.read_text("/a") == "\ufeffhi"
    with pytest.raises(UnicodeDecodeError):
        await fs.read_text("/b")


@pytest.mark.anyio
async def test_kill_sends_escalate_after_in_milliseconds() -> None:
    transport = Transport([None, None, None])
    handle = AsyncProcessService("sbx", transport).connect("proc")  # type: ignore[arg-type]

    await handle.kill(signal="TERM", escalate_after=1.5)
    await handle.kill(signal="TERM", escalate_after=0.0001)
    await handle.kill()

    assert [spec.json_body for spec in transport.specs] == [
        {"signal": "SIGTERM", "escalateAfterMs": 1500, "escalateTo": "SIGKILL"},
        {"signal": "SIGTERM", "escalateAfterMs": 1, "escalateTo": "SIGKILL"},
        {"signal": "SIGTERM"},
    ]


@pytest.mark.anyio
async def test_presigned_urls_read_a_nanosecond_expiry_on_every_python() -> None:
    async def signer(method: str, path: str, expires: int) -> JsonValue:
        return {"signature": "v1_ok", "expires_at": "2026-06-12T03:22:47.123456789Z"}

    transport = Transport()
    transport.base_url = "http://toolbox.invalid"  # type: ignore[attr-defined]
    fs = AsyncFileSystemService(transport, _file_url_signer=signer)  # type: ignore[arg-type]

    url = await fs.presigned_download_url("/a")

    assert url == "http://toolbox.invalid/files/download?path=%2Fa&expires=1781234567&signature=v1_ok"


def test_connect_session_refuses_a_non_string_non_mapping_identity() -> None:
    process = AsyncProcessService("sbx", Transport())  # type: ignore[arg-type]

    for bad in (5, ""):
        with pytest.raises(BadRequestError, match="session identity needs a non-empty string sessionId"):
            process.connect_session(bad)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_lsp_rejects_unknown_position_keys_and_names_a_malformed_documentation() -> None:
    transport = Transport([{"isIncomplete": False, "items": [{"label": "x", "documentation": 7}]}])
    server = AsyncLspServer(transport, "python", "/p")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="LSP position: unknown key 'col'"):
        await server.completions("file:///a.py", {"line": 0, "col": 1})  # type: ignore[typeddict-unknown-key]
    assert transport.specs == []
    with pytest.raises(BadRequestError, match="LSP completion list: documentation must be"):
        await server.completions("file:///a.py", LspPosition(line=0, character=0))


@pytest.mark.anyio
async def test_huge_durations_mean_no_limit_and_never_overflow() -> None:
    from daytona.v2._core import timeout_millis_from_seconds, wait_budget

    for huge in (1e308, 10**400):
        assert wait_budget(huge) == 0
    assert timeout_millis_from_seconds(9_007_199_254_740) == 9_007_199_254_740_000
    for too_large in (10**400, 9_007_199_254_741, 1e300):
        with pytest.raises(BadRequestError, match="timeout is too large"):
            timeout_millis_from_seconds(too_large)
    with pytest.raises(BadRequestError, match="escalate_after is too large"):
        timeout_millis_from_seconds(1e308, "escalate_after")
    for bad in (float("inf"), float("nan"), -1, True, "1"):
        with pytest.raises(BadRequestError, match="must be a non-negative finite number"):
            wait_budget(bad)  # type: ignore[arg-type]

    transport = Transport([_process(), _process(), {}, {}])
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]
    await process.start("x", execution_timeout=1e308)
    await process.start("x", execution_timeout=10**400)
    git = AsyncGitService(transport)  # type: ignore[arg-type]
    await git.pull("/repo", timeout=1e308)
    await git.pull("/repo", timeout=10**400)
    assert all("timeoutMs" not in (spec.json_body or {}) for spec in transport.specs[:2])
    # The git deadline is the SDK's own: huge values and 0 mean no limit.
    assert [spec.timeout for spec in transport.specs[2:4]] == [0, 0]
    with pytest.raises(BadRequestError):
        await git.pull("/repo", timeout=-1)


@pytest.mark.anyio
async def test_a_git_remote_timeout_of_zero_is_no_limit_like_every_other_timeout() -> None:
    transport = Transport([{}, {}, {}])
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    await git.clone("https://example.invalid/r.git", "/r", timeout=0)
    await git.pull("/r", timeout=0)
    await git.push("/r", timeout=0)

    assert [spec.timeout for spec in transport.specs] == [0, 0, 0]


@pytest.mark.parametrize("argv", ["ls -la", [], ["ls", 5], {"ls": "-la"}, (part for part in ["ls"])])
@pytest.mark.anyio
async def test_start_refuses_an_argv_that_is_not_a_list_of_strings(argv: object) -> None:
    transport = Transport()
    with pytest.raises(BadRequestError, match="argv must be a non-empty sequence of strings"):
        await AsyncProcessService("sbx", transport).start(argv=argv)  # type: ignore[arg-type]
    assert transport.specs == []


@pytest.mark.parametrize("utilization", [-0.1, 1.5])
def test_system_metrics_refuse_a_cpu_utilization_outside_0_to_1(utilization: float) -> None:
    from daytona.v2._core import expect_system_metrics

    metrics = {
        "timestamp": "2026-01-01T00:00:00Z",
        "cpuCount": 1,
        "cpuUtilization": utilization,
        "memoryUsageBytes": 0,
        "memoryTotalBytes": 0,
        "diskUsageBytes": 0,
        "diskTotalBytes": 0,
    }
    with pytest.raises(BadRequestError, match="cpuUtilization must be between 0 and 1"):
        expect_system_metrics(metrics)


@pytest.mark.anyio
async def test_list_shape_names_the_malformed_item() -> None:
    fs = AsyncFileSystemService(Transport([{"items": [{"file": "/a", "line": 1, "content": "x"}, 5]}]))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match=r"grep response: items\[1\] must be an object"):
        await fs.grep("/", "x")


def test_read_many_refuses_an_error_part_with_a_byte_order_mark() -> None:
    from daytona.v2._core import parse_bulk_read_result

    with pytest.raises(BadRequestError, match="bulk download error part: the data is not JSON"):
        parse_bulk_read_result(_error_part(b'\xef\xbb\xbf{"path": "/a", "message": "m"}'))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"kernel": "python"}, "session response: id is required"),
        (
            {"id": "", "kernel": "python"},
            'session response: id is required \\(context "mine" may have been created\\)',
        ),
        ({"id": 5, "kernel": "python"}, "session response: id must be a string"),
        ({"id": "c"}, "session response: kernel is required"),
    ],
)
async def test_create_context_checks_the_session_fields_it_reads(payload: JsonValue, message: str) -> None:
    transport = Transport([payload])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]
    context_id = "mine" if "may have been" in message else None

    with pytest.raises(BadRequestError, match=message):
        await interpreter.create_context(language="python", context_id=context_id)


@pytest.mark.anyio
async def test_list_contexts_reads_only_id_and_kernel() -> None:
    sessions = {
        "items": [{"id": "c", "kernel": "python", "createdAt": "yesterday"}, {"id": "sh", "kernel": "shell"}]
    }
    transport = Transport([sessions, {"items": [{"id": "c"}]}])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    assert [(c.context_id, c.language) for c in await interpreter.list_contexts()] == [("c", "python")]
    with pytest.raises(BadRequestError, match="session response: kernel is required"):
        await interpreter.list_contexts()


@pytest.mark.anyio
@pytest.mark.parametrize("cursor", ['""', "5", "null"])
async def test_stream_logs_refuses_a_present_invalid_cursor_including_null(cursor: str) -> None:
    lines = ["id: c1", "event: eof", f'data: {{"cursor":{cursor}}}', ""]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="eof event: cursor must be a non-empty string"):
        async for _event in handle.stream_logs():
            pass


def _error_part(body: bytes) -> bytes:
    return (
        b"--DAYTONA-FILE-BOUNDARY\r\n"
        b'Content-Disposition: form-data; name="error"\r\n'
        b"Content-Type: application/json\r\n\r\n" + body + b"\r\n--DAYTONA-FILE-BOUNDARY--\r\n"
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not-json", "bulk download error part: the data is not JSON"),
        (b"[]", "expected bulk download error part"),
        (b'{"path": 42, "message": "m"}', "bulk download error part: path must be a string"),
        (
            b'{"path": "/a", "message": {"unexpected": true}}',
            "bulk download error part: message must be a string",
        ),
        (b'{"path": "/a", "message": "m", "code": 99}', "bulk download error part: code must be a string"),
    ],
)
def test_read_many_rejects_a_malformed_error_part(body: bytes, message: str) -> None:
    from daytona.v2._core import parse_bulk_read_result

    with pytest.raises(BadRequestError, match=message):
        parse_bulk_read_result(_error_part(body))


def test_read_many_reads_a_contract_error_part() -> None:
    from daytona.v2._core import parse_bulk_read_result
    from daytona.v2.types import BulkReadError

    result = parse_bulk_read_result(
        _error_part(b'{"path": "/a", "message": "missing", "code": "FILE_NOT_FOUND"}')
    )

    assert result.errors == [BulkReadError(path="/a", message="missing", code="FILE_NOT_FOUND", source=None)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        (
            "status",
            {"currentBranch": "m", "ahead": 0, "behind": 0, "branchPublished": True, "fileStatus": 42},
            "fileStatus must be a list",
        ),
        (
            "status",
            {"currentBranch": "m", "ahead": 0, "behind": 0, "branchPublished": True, "fileStatus": {}},
            "fileStatus must be a list",
        ),
        ("get_config", {"value": 42}, "value must be a string or null"),
        ("get_config", None, "expected JSON object"),
        ("start", {"message": "ok", "status": "wrong"}, "status must be a map of process statuses"),
        ("stop", {"message": "ok", "status": [1]}, "status must be a map of process statuses"),
        ("start", {"message": "ok", "status": {"wm": "x"}}, "status must be a map of process statuses"),
        ("glob", ["/a"], "expected glob response"),
        ("glob", {"items": ["/a"]}, "glob response: files is required"),
        ("glob", {"files": [42]}, "files must be a list of strings"),
    ],
)
async def test_response_paths_reject_what_breaks_the_contract(
    operation: str, payload: JsonValue, message: str
) -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    transport = Transport([payload])
    git = AsyncGitService(transport)  # type: ignore[arg-type]
    cu = AsyncComputerUseService(transport)  # type: ignore[arg-type]
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    calls = {
        "status": lambda: git.status("/repo"),
        "get_config": lambda: git.get_config("user.name"),
        "start": lambda: cu.start(),
        "stop": lambda: cu.stop(),
        "glob": lambda: fs.glob("/", "*"),
    }

    with pytest.raises(BadRequestError, match=message) as info:
        await calls[operation]()
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_git_config_value_reads_a_string_or_null() -> None:
    git = AsyncGitService(Transport([{"value": "Ada"}, {"value": None}, {}]))  # type: ignore[arg-type]

    assert [await git.get_config("user.name") for _ in range(3)] == ["Ada", None, None]


@pytest.mark.anyio
async def test_display_windows_rejects_an_id_above_the_contract_maximum() -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    window = {"id": 9007199254740992, "title": "t", "x": 0, "y": 0, "width": 1, "height": 1, "isActive": True}
    cu = AsyncComputerUseService(Transport([{"windows": [window]}]))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="less than or equal to 9007199254740991"):
        await cu.display.windows()


@pytest.mark.anyio
async def test_addon_process_restart_backoff_is_in_seconds() -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    status = {
        "Running": True,
        "Priority": 1,
        "AutoRestart": True,
        "Failed": False,
        "RestartAttempts": 2,
        "RestartBackoffMs": 1500,
        "Status": "running",
    }
    statuses = await AsyncComputerUseService(Transport([{"wm": status}])).service.status()  # type: ignore[arg-type]

    assert statuses["wm"].restart_backoff == 1.5


@pytest.mark.anyio
async def test_sse_blocks_without_data_and_an_unterminated_final_block_dispatch_nothing() -> None:
    lines = [
        "event: log",
        'data: {"cursor":"c1","channel":"stdout","data":"x","timestamp":1}',
        "",
        "event: eof",
        "id: c2",
        "",
        "event: eof",
        'data: {"cursor":"c3"}',
    ]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]
    seen: list[str] = []

    # Neither eof block dispatches, so the stream ends without eof: cut short.
    with pytest.raises(ConnectionError, match="ended before eof"):
        async for event in handle.stream_logs():
            seen.append(event.type)
    assert seen == ["log"]


@pytest.mark.anyio
async def test_a_log_stream_that_ends_without_eof_raises_a_connection_error() -> None:
    lines = ["event: log", 'data: {"cursor":"c1","channel":"stdout","data":"x","timestamp":1}', ""]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]
    seen: list[str] = []

    with pytest.raises(ConnectionError, match="resume from the last event's cursor") as info:
        async for event in handle.stream_logs():
            seen.append(event.cursor)
    assert seen == ["c1"]
    assert info.value.source == "SDK"


@pytest.mark.anyio
async def test_stream_logs_sends_the_same_headers_as_the_typescript_sdk() -> None:
    from daytona.v2._core import logs_spec

    assert logs_spec("p", cursor="c1", limit=None, encoding="text", follow=True).headers == {
        "Accept": "text/event-stream",
        "Last-Event-ID": "c1",
    }
    assert logs_spec("p", cursor=None, limit=None, encoding="text", follow=True).headers == {
        "Accept": "text/event-stream"
    }
    assert logs_spec("p", cursor="c1", limit=None, encoding="text", follow=False).headers == {}


@pytest.mark.anyio
async def test_stream_logs_uses_the_sse_id_only_when_the_cursor_key_is_missing() -> None:
    lines = ["id: c1", "event: eof", "data: {}", ""]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    assert [event async for event in handle.stream_logs()] == [StreamLogEofEvent(cursor="c1")]


@pytest.mark.anyio
async def test_a_stream_error_event_raises_its_typed_error_even_without_a_cursor() -> None:
    lines = [
        "event: error",
        'data: {"message":"gone","code":"PROCESS_NOT_FOUND","source":"DAYTONA_DAEMON"}',
        "",
    ]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    with pytest.raises(ProcessNotFoundError):
        async for _event in handle.stream_logs():
            pass


@pytest.mark.anyio
@pytest.mark.parametrize("backoff", [10**400, 2**53, -1])
async def test_an_out_of_range_restart_backoff_is_the_malformed_response_error(backoff: int) -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    status = {
        "Running": True,
        "Priority": 1,
        "AutoRestart": True,
        "Failed": False,
        "RestartAttempts": 0,
        "RestartBackoffMs": backoff,
        "Status": "running",
    }
    cu = AsyncComputerUseService(Transport([{"wm": status}]))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="RestartBackoffMs") as info:
        await cu.service.status()
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


def test_read_many_refuses_an_error_part_that_is_not_utf_8() -> None:
    from daytona.v2._core import parse_bulk_read_result

    with pytest.raises(BadRequestError, match="bulk download error part: the data is not JSON"):
        parse_bulk_read_result(_error_part(b'{"path": "/a", "message": "\xff"}'))


@pytest.mark.parametrize(("context_id", "language"), [("c", 5), ("c", ""), (5, "python"), ("", "python")])
def test_connect_context_refuses_an_identity_it_could_not_write_back(
    context_id: object, language: object
) -> None:
    interpreter = AsyncInterpreterService("sbx", Transport(), AsyncProcessService("sbx", Transport()))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="interpreter context"):
        interpreter.connect_context(context_id, language=language)  # type: ignore[arg-type]


def test_a_reconnected_context_identity_round_trips() -> None:
    interpreter = AsyncInterpreterService("sbx", Transport(), AsyncProcessService("sbx", Transport()))  # type: ignore[arg-type]

    context = interpreter.connect_context("c", language="python")

    assert interpreter.connect_context(context.to_json()).to_json() == {
        "sandboxId": "sbx",
        "contextId": "c",
        "language": "python",
    }


@pytest.mark.anyio
async def test_stream_logs_stops_reading_after_eof() -> None:
    lines = [
        "event: eof",
        'data: {"cursor":"c1"}',
        "",
        "event: log",
        'data: {"cursor":"c2","channel":"stdout","data":"late","timestamp":1}',
        "",
        "event: error",
        'data: {"message":"never read"}',
        "",
    ]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    assert [event async for event in handle.stream_logs()] == [StreamLogEofEvent(cursor="c1")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ('{"cursor":"c","reason":"exited","exitCode":0}', "id"),
        (
            '{"id":"proc","kind":"exec","state":"terminal","cwd":"/","stdin":"closed","keepLogs":"on_exit_ttl","createdAt":"yesterday","truncatedHead":false,"reason":"exited","exitCode":7}',
            "createdAt",
        ),
    ],
)
async def test_a_state_event_is_checked_as_the_whole_process_record(payload: str, field: str) -> None:
    lines = ["id: c1", "event: state", f"data: {payload}", ""]
    handle = AsyncProcessService("sbx", Transport(lines=lines)).connect("proc")  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match=field) as info:
        async for _event in handle.stream_logs():
            pass
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.parametrize(("kind", "expected"), [("pty", "pty"), ("future", None), (None, None)])
def test_connect_reads_a_known_kind_and_drops_an_unknown_string(
    kind: Optional[str], expected: Optional[str]
) -> None:
    identity: dict[str, object] = {"sandboxId": "sbx", "processId": "p"}
    if kind is not None:
        identity["kind"] = kind
    handle = AsyncProcessService("sbx", Transport()).connect(identity)  # type: ignore[arg-type]

    assert handle.to_json().get("kind") == expected


@pytest.mark.parametrize("kind", [5, True, ["pty"]])
def test_connect_refuses_a_kind_that_is_not_a_string(kind: object) -> None:
    with pytest.raises(BadRequestError, match="kind must be a string"):
        AsyncProcessService("sbx", Transport()).connect({"sandboxId": "sbx", "processId": "p", "kind": kind})  # type: ignore[arg-type,typeddict-item]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        ("list", {"items": [], "nextCursor": 5}, "file list response: nextCursor must be a string"),
        ("history", {"items": [], "nextCursor": True}, "git history response: nextCursor must be a string"),
        ("remotes", {"remotes": [{"name": 5, "url": "u"}]}, "git remote: name must be a string"),
        ("remotes", {"remotes": [{"name": "origin"}]}, "git remote: url is required"),
    ],
)
async def test_list_cursors_and_remotes_name_what_is_wrong(
    operation: str, payload: JsonValue, message: str
) -> None:
    transport = Transport([payload])
    calls = {
        "list": lambda: AsyncFileSystemService(transport).list("/"),  # type: ignore[arg-type]
        "history": lambda: AsyncGitService(transport).history("/r"),  # type: ignore[arg-type]
        "remotes": lambda: AsyncGitService(transport).list_remotes("/r"),  # type: ignore[arg-type]
    }

    with pytest.raises(BadRequestError, match=message):
        await calls[operation]()


def test_the_wire_duration_limit_is_checked_before_rounding_like_the_typescript_sdk() -> None:
    from daytona.v2._core import timeout_millis_from_seconds

    assert timeout_millis_from_seconds(9_007_199_254_740.991) is not None
    with pytest.raises(BadRequestError, match="timeout is too large"):
        timeout_millis_from_seconds(9_007_199_254_740.992)


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01T24:00:00Z",
        "2026-01-01T23:60:00Z",
        "2026-12-31T23:59:60Z",
        "2026-13-01T00:00:00Z",
        "2026-00-01T00:00:00Z",
        "2026-01-00T00:00:00Z",
        "2026-01-32T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-01-01T00:00:00+24:00",
        "2026-01-01T00:00:00+05:60",
        "0000-01-01T00:00:00Z",
    ],
)
def test_timestamp_field_ranges_match_the_typescript_sdk_exactly(value: str) -> None:
    from daytona.v2._timestamps import parse_timestamp

    with pytest.raises(BadRequestError, match="not an RFC 3339 timestamp"):
        parse_timestamp(value, "field")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("escalate_after", "message"),
    [(-1, "escalate_after must be a non-negative"), (1e13, "escalate_after is too large")],
)
async def test_kill_names_escalate_after_in_its_errors(escalate_after: float, message: str) -> None:
    transport = Transport()
    with pytest.raises(BadRequestError, match=message):
        await AsyncProcessService("sbx", transport).connect("p").kill(escalate_after=escalate_after)  # type: ignore[arg-type]
    assert transport.specs == []


@pytest.mark.anyio
async def test_a_created_process_record_with_an_empty_id_is_refused() -> None:
    with pytest.raises(BadRequestError, match="process response: id is required"):
        await AsyncProcessService("sbx", Transport([_process(id="")])).start("x")  # type: ignore[arg-type]


@pytest.mark.parametrize("filename", ["%zz", "%ff"])
def test_read_many_refuses_a_file_part_with_a_bad_filename_star(filename: str) -> None:
    from daytona.v2._core import parse_bulk_read_result

    part = (
        b"--DAYTONA-FILE-BOUNDARY\r\n"
        b"Content-Disposition: form-data; name=\"file\"; filename*=utf-8''" + filename.encode() + b"\r\n\r\n"
        b"x\r\n--DAYTONA-FILE-BOUNDARY--\r\n"
    )
    with pytest.raises(BadRequestError, match="filename\\* is not valid percent-encoded UTF-8"):
        parse_bulk_read_result(part)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "context",
    [
        5,
        None,
        "",
        {"sandboxId": "sbx", "contextId": "", "language": "python"},
        {"sandbox_id": "sbx", "context_id": "", "language": "python"},
    ],
)
async def test_delete_context_refuses_an_id_that_is_not_a_non_empty_string(context: object) -> None:
    transport = Transport()
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="interpreter context id must be a non-empty string"):
        await interpreter.delete_context(context)  # type: ignore[arg-type]
    assert transport.specs == []


@pytest.mark.anyio
async def test_pty_start_refuses_a_terminal_that_is_not_a_terminal_size() -> None:
    transport = Transport()
    pty = AsyncPtyService(AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="terminal must be a TerminalSize"):
        await pty.start(terminal={"cols": 80, "rows": 24})  # type: ignore[arg-type]
    assert transport.specs == []


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["system", "", None])
async def test_git_config_refuses_a_scope_other_than_global_or_local(scope: object) -> None:
    transport = Transport()
    git = AsyncGitService(transport)  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="scope must be 'global' or 'local'"):
        await git.get_config("user.name", scope=scope)  # type: ignore[arg-type]
    with pytest.raises(BadRequestError, match="scope must be 'global' or 'local'"):
        await git.set_config("user.name", "Ada", scope=scope)  # type: ignore[arg-type]
    assert transport.specs == []


@pytest.mark.anyio
async def test_git_config_sends_a_str_enum_scope_as_its_plain_value() -> None:
    import enum

    class Scope(str, enum.Enum):
        GLOBAL = "global"
        LOCAL = "local"

    transport = Transport([{"value": None}, None])
    git = AsyncGitService(transport)  # type: ignore[arg-type]
    await git.get_config("user.name", scope=Scope.GLOBAL)  # type: ignore[arg-type]
    await git.set_config("user.name", "Ada", scope=Scope.LOCAL, path="/repo")  # type: ignore[arg-type]

    assert transport.specs[0].params is not None and type(transport.specs[0].params["scope"]) is str
    assert transport.specs[0].params["scope"] == "global"
    body = transport.specs[1].json_body
    assert isinstance(body, dict) and type(body["scope"]) is str and body["scope"] == "local"


@pytest.mark.anyio
async def test_git_config_local_without_path_is_forwarded_for_the_toolbox_to_judge() -> None:
    transport = Transport([{"value": None}])
    await AsyncGitService(transport).get_config("user.name", scope="local")  # type: ignore[arg-type]
    assert transport.specs[0].params == {"key": "user.name", "scope": "local"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event", "message"),
    [
        ('{"type":"create","name":"a","timestamp":1}', "isDir is required"),
        ('{"type":"create","name":"a","isDir":1,"timestamp":1}', "isDir must be a boolean"),
    ],
)
async def test_watch_event_errors_say_required_or_wrong_type(event: str, message: str) -> None:
    stream = _WatchStream(["event: start", "data: {}", "", "event: fs", f"data: {event}", ""])
    handle = await AsyncFileSystemService(_WatchTransport(stream)).watch_dir("/w", lambda _e: None)  # type: ignore[arg-type]

    error = await asyncio.wait_for(handle.wait_for_exit(), timeout=2)
    assert isinstance(error, BadRequestError)
    assert message in str(error)


@pytest.mark.anyio
async def test_a_bare_array_list_names_the_malformed_item() -> None:
    fs = AsyncFileSystemService(Transport([[{"file": "/a", "line": 1, "content": "x"}, 5]]))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match=r"grep response: items\[1\] must be an object"):
        await fs.grep("/", "x")


@pytest.mark.anyio
async def test_start_accepts_a_tuple_argv() -> None:
    transport = Transport([_process()])
    await AsyncProcessService("sbx", transport).start(argv=("ls", "-la"))  # type: ignore[arg-type]
    assert transport.specs[0].json_body["argv"] == ["ls", "-la"]  # type: ignore[index]


@pytest.mark.anyio
async def test_an_isolated_run_runs_in_and_deletes_the_id_the_sandbox_returned() -> None:
    session = {"id": "server-chosen", "kernel": "python"}
    process = _process(id="code", kind="code")
    transport = Transport(
        [
            session,
            process,
            {"reason": "exited", "exitCode": 0},
            {"frames": [], "nextCursor": "0", "truncatedHead": False, "eof": True},
            None,
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    await interpreter.run("1", isolated=True)

    code_spec = next(spec for spec in transport.specs if spec.path == "/processes")
    assert code_spec.json_body["sessionId"] == "server-chosen"  # type: ignore[index]
    assert transport.specs[-1].method == "DELETE"
    assert transport.specs[-1].path == "/sessions/server-chosen"


@pytest.mark.anyio
async def test_list_contexts_says_items_must_be_a_list() -> None:
    transport = Transport([{"items": "x"}])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="session list response: items must be a list"):
        await interpreter.list_contexts()


def _a11y_chain(levels: int) -> dict[str, object]:
    """A tree ``levels`` nodes deep (the root is level 0), built without recursion."""
    node: dict[str, object] = {
        "id": "leaf",
        "role": "r",
        "name": "n",
        "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
        "states": [],
    }
    for index in range(levels - 1):
        node = {
            "id": f"n{index}",
            "role": "r",
            "name": "n",
            "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
            "states": [],
            "children": [node],
        }
    return node


def _a11y_depth(node: object) -> int:
    depth = 0
    while getattr(node, "children", None):
        node = node.children[0]  # type: ignore[attr-defined]
        depth += 1
    return depth


@pytest.mark.anyio
async def test_a11y_tree_sends_the_256_level_cap_by_default_and_maps_a_256_level_tree() -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    transport = Transport([{"root": _a11y_chain(257), "truncated": True}])
    tree = await AsyncComputerUseService(transport).a11y.tree(scope="focused")  # type: ignore[arg-type]

    assert transport.specs[0].params == {"scope": "focused", "maxDepth": 256}
    assert _a11y_depth(tree.root) == 256
    assert tree.truncated is True


@pytest.mark.anyio
@pytest.mark.parametrize(("levels", "max_depth"), [(10_000, None), (258, None), (4, 2)])
async def test_a11y_tree_refuses_a_response_deeper_than_requested(
    levels: int, max_depth: Optional[int]
) -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    cu = AsyncComputerUseService(Transport([{"root": _a11y_chain(levels), "truncated": False}]))  # type: ignore[arg-type]
    limit = 256 if max_depth is None else max_depth

    with pytest.raises(BadRequestError, match=f"nested deeper than {limit} levels") as info:
        await cu.a11y.tree(scope="focused", max_depth=max_depth)
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.anyio
async def test_a11y_find_refuses_a_match_deeper_than_the_cap() -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    cu = AsyncComputerUseService(Transport([{"matches": [_a11y_chain(10_000)], "truncated": False}]))  # type: ignore[arg-type]

    with pytest.raises(BadRequestError, match="nested deeper than 256 levels"):
        await cu.a11y.find(scope="all")


@pytest.mark.anyio
@pytest.mark.parametrize("max_depth", [257, -1, 1.5, True])
async def test_a11y_tree_refuses_a_max_depth_outside_0_to_256_before_any_request(max_depth: object) -> None:
    from daytona.v2.computer_use import AsyncComputerUseService

    transport = Transport()
    with pytest.raises(BadRequestError, match="max_depth must be an integer between 0 and 256"):
        await AsyncComputerUseService(transport).a11y.tree(scope="focused", max_depth=max_depth)  # type: ignore[arg-type]
    assert transport.specs == []


def test_a_json_body_nested_10000_levels_deep_is_the_malformed_response_error() -> None:
    from daytona.v2._core import parse_json_bytes, sse_json

    # Deep enough for every supported Python's decoder to hit its recursion limit.
    body = '{"root":' * 100_000 + "1" + "}" * 100_000
    for decode in (
        lambda: parse_json_bytes(body.encode()),
        lambda: sse_json("log", body, "process log log event"),
    ):
        with pytest.raises(BadRequestError, match="nested too deeply") as info:
            decode()
        assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")


def test_a_10000_level_tree_body_is_refused_whichever_step_stops_it() -> None:
    """Older Pythons stop it in the JSON decoder, newer ones decode it and the
    iterative mapper refuses it: either way the malformed-response error."""
    from daytona.v2._core import parse_json_bytes
    from daytona.v2.computer_use_core import parse_computer_use_a11y_tree_payload

    node = '{"id":"n","role":"r","name":"n","bounds":{"x":0,"y":0,"width":1,"height":1},"states":[]'
    body = (
        '{"truncated":false,"root":' + (node + ',"children":[') * 9_999 + node + "}" + "]}" * 9_999 + "}"
    ).encode()
    with pytest.raises(BadRequestError) as info:
        parse_computer_use_a11y_tree_payload(parse_json_bytes(body), max_depth=256)
    assert (info.value.status_code, info.value.code, info.value.source) == (400, "BAD_REQUEST", "SDK")
