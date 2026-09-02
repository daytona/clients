# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from daytona.common.errors import DaytonaProcessCursorExpiredError
from daytona.common.process import CodeRunParams
from daytona_toolbox_api_client import Chart as GeneratedChart
from daytona_toolbox_api_client.exceptions import ApiException as SyncToolboxApiException
from daytona_toolbox_api_client_async.exceptions import ApiException as AsyncToolboxApiException


@dataclass
class _FakeSyncResponse:
    status_code: int
    lines: list[str]
    body: str = ""
    headers: dict[str, str] | None = None

    def iter_lines(self):
        return iter(self.lines)

    def read(self) -> bytes:
        return self.body.encode("utf-8")


@dataclass
class _FakeSyncStreamContext:
    response: _FakeSyncResponse

    def __enter__(self) -> _FakeSyncResponse:
        return self.response

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@dataclass
class _SilentSyncResponse:
    """Streams ``lines`` and then behaves like an idle SSE stream whose transport read
    timeout fired - which is all httpx surfaces when the daemon sends nothing."""

    lines: list[str] = field(default_factory=list)
    status_code: int = 200
    headers: dict[str, str] | None = None

    def iter_lines(self) -> Iterator[str]:
        yield from self.lines
        raise httpx.ReadTimeout("timed out while reading the stream")

    def read(self) -> bytes:
        return b""


class _HangingAsyncContent:
    """Yields ``lines`` and then never completes, like a live stream with no new output."""

    def __init__(self, lines: list[str]):
        self._lines = [line.encode("utf-8") for line in lines]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._index < len(self._lines):
            value = self._lines[self._index]
            self._index += 1
            return value
        await asyncio.sleep(30)
        raise StopAsyncIteration


@dataclass
class _HangingAsyncResponse:
    status: int = 200
    lines: list[str] = field(default_factory=list)
    headers: dict[str, str] | None = None
    closed: bool = False

    def __post_init__(self) -> None:
        self.content = _HangingAsyncContent(self.lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def text(self) -> str:
        return ""


class _FakeAsyncContent:
    def __init__(self, lines: list[str]):
        self._lines = [line.encode("utf-8") for line in lines]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        value = self._lines[self._index]
        self._index += 1
        return value


@dataclass
class _FakeAsyncResponse:
    status: int
    lines: list[str]
    body: str = ""
    headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        self.content = _FakeAsyncContent(self.lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def text(self) -> str:
        return self.body


class TestSyncProcessExec:
    def _make_process(self):
        from daytona._sync.process import Process

        mock_api = MagicMock()
        return Process("python", mock_api, http_client=MagicMock()), mock_api

    def test_exec_simple_command(self):
        proc, api = self._make_process()
        api.execute_command.return_value = MagicMock(result="Hello, World!", exit_code=0, additional_properties={})
        result = proc.exec("echo 'Hello, World!'")
        assert result.exit_code == 0
        assert result.result == "Hello, World!"

    def test_exec_with_cwd_and_env(self):
        proc, api = self._make_process()
        api.execute_command.return_value = MagicMock(result="value", exit_code=0, additional_properties={})
        proc.exec("echo $MY_VAR", cwd="/workspace", env={"MY_VAR": "value"})
        request = api.execute_command.call_args.kwargs["request"]
        assert request.command == "echo $MY_VAR"
        assert request.cwd == "/workspace"
        assert request.envs == {"MY_VAR": "value"}

    def test_exec_falls_back_to_additional_properties_code(self):
        proc, api = self._make_process()
        api.execute_command.return_value = MagicMock(result="oops", exit_code=None, additional_properties={"code": 42})
        result = proc.exec("false")
        assert result.exit_code == 42

    def test_code_run_uses_language_and_params(self):
        proc, api = self._make_process()
        api.code_run.return_value = MagicMock(
            result="42\n",
            exit_code=0,
            artifacts=None,
            additional_properties={},
        )
        result = proc.code_run("print(42)", params=CodeRunParams(argv=["--flag"], env={"DEBUG": "1"}), timeout=5)
        request = api.code_run.call_args.kwargs["request"]
        assert request.language == "python"
        assert request.argv == ["--flag"]
        assert request.envs == {"DEBUG": "1"}
        assert request.timeout == 5
        assert result.result == "42\n"

    def test_code_run_parses_charts(self):
        proc, api = self._make_process()
        api.code_run.return_value = MagicMock(
            result="chart output",
            exit_code=0,
            artifacts=MagicMock(charts=[GeneratedChart(type="line", title="Line", elements=[])]),
            additional_properties={},
        )
        result = proc.code_run("print('chart')")
        assert result.artifacts is not None
        assert result.artifacts.charts is not None
        assert len(result.artifacts.charts) == 1
        assert result.artifacts.charts[0].title == "Line"


class TestSyncProcessSessions:
    def _make_process(self):
        from daytona._sync.process import Process

        mock_api = MagicMock()
        return Process("python", mock_api, http_client=MagicMock()), mock_api

    def test_create_session(self):
        proc, api = self._make_process()
        proc.create_session("my-session")
        request = api.create_session.call_args.kwargs["request"]
        assert request.session_id == "my-session"

    def test_get_session(self):
        proc, api = self._make_process()
        api.get_session.return_value = MagicMock(session_id="my-session")
        assert proc.get_session("my-session").session_id == "my-session"

    def test_execute_session_command(self):
        proc, api = self._make_process()
        api.session_execute_command.return_value = MagicMock(
            cmd_id="cmd-1",
            output="all",
            stdout="out",
            stderr="err",
            exit_code=0,
            additional_properties={},
        )
        result = proc.execute_session_command("my-session", req=MagicMock())
        assert result.cmd_id == "cmd-1"
        assert result.stdout == "out"
        assert result.stderr == "err"

    def test_get_session_command_logs(self):
        proc, api = self._make_process()
        api.get_session_command_logs.return_value = MagicMock(output="all", stdout="out", stderr="err")
        result = proc.get_session_command_logs("my-session", "cmd-1")
        assert result.output == "all"
        assert result.stdout == "out"
        assert result.stderr == "err"

    def test_delete_session(self):
        proc, api = self._make_process()
        proc.delete_session("my-session")
        api.delete_session.assert_called_once_with(session_id="my-session", _request_timeout=None)

    def test_get_entrypoint_session(self):
        proc, api = self._make_process()
        api.get_entrypoint_session.return_value = MagicMock(session_id="entrypoint")

        assert proc.get_entrypoint_session().session_id == "entrypoint"

    def test_get_session_command(self):
        proc, api = self._make_process()
        api.get_session_command.return_value = MagicMock(id="cmd-1")

        assert proc.get_session_command("my-session", "cmd-1").id == "cmd-1"

    def test_get_entrypoint_logs(self):
        proc, api = self._make_process()
        api.get_entrypoint_logs.return_value = MagicMock(output="all", stdout="out", stderr="err")

        result = proc.get_entrypoint_logs()

        assert result.output == "all"
        assert result.stdout == "out"
        assert result.stderr == "err"

    def test_send_session_command_input_and_list_sessions(self):
        proc, api = self._make_process()
        api.list_sessions.return_value = [MagicMock(session_id="one")]

        proc.send_session_command_input("my-session", "cmd-1", "hello")
        sessions = proc.list_sessions()

        assert sessions[0].session_id == "one"
        send_request = api.send_input.call_args.kwargs["request"]
        assert send_request.data == "hello"


class TestAsyncProcessExec:
    def _make_process(self):
        from daytona._async.process import AsyncProcess

        mock_api = AsyncMock()
        return AsyncProcess("python", mock_api), mock_api

    @pytest.mark.asyncio
    async def test_exec_simple(self):
        proc, api = self._make_process()
        api.execute_command.return_value = MagicMock(result="output", exit_code=0, additional_properties={})
        result = await proc.exec("echo hello")
        assert result.exit_code == 0
        assert result.result == "output"

    @pytest.mark.asyncio
    async def test_exec_with_env(self):
        proc, api = self._make_process()
        api.execute_command.return_value = MagicMock(result="value", exit_code=0, additional_properties={})
        await proc.exec("echo $MY_VAR", env={"MY_VAR": "value"})
        request = api.execute_command.call_args.kwargs["request"]
        assert request.envs == {"MY_VAR": "value"}

    @pytest.mark.asyncio
    async def test_code_run(self):
        proc, api = self._make_process()
        api.code_run.return_value = MagicMock(result="1", exit_code=0, artifacts=None, additional_properties={})
        result = await proc.code_run("print(1)")
        request = api.code_run.call_args.kwargs["request"]
        assert request.language == "python"
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_create_and_delete_session(self):
        proc, api = self._make_process()
        await proc.create_session("my-session")
        request = api.create_session.call_args.kwargs["request"]
        assert request.session_id == "my-session"
        await proc.delete_session("my-session")
        api.delete_session.assert_called_once_with(session_id="my-session", _request_timeout=None)

    @pytest.mark.asyncio
    async def test_execute_session_command(self):
        proc, api = self._make_process()
        api.session_execute_command.return_value = MagicMock(
            cmd_id="cmd-1",
            output="all",
            stdout="out",
            stderr="err",
            exit_code=0,
            additional_properties={},
        )
        result = await proc.execute_session_command("my-session", req=MagicMock())
        assert result.cmd_id == "cmd-1"
        assert result.output == "all"

    @pytest.mark.asyncio
    async def test_get_entrypoint_and_session_metadata(self):
        proc, api = self._make_process()
        api.get_entrypoint_session.return_value = MagicMock(session_id="entrypoint")
        api.get_session_command.return_value = MagicMock(id="cmd-1")
        api.get_entrypoint_logs.return_value = MagicMock(output="all", stdout="out", stderr="err")
        api.list_sessions.return_value = [MagicMock(session_id="one")]

        assert (await proc.get_entrypoint_session()).session_id == "entrypoint"
        assert (await proc.get_session_command("my-session", "cmd-1")).id == "cmd-1"
        assert (await proc.get_entrypoint_logs()).stdout == "out"
        assert (await proc.list_sessions())[0].session_id == "one"

    @pytest.mark.asyncio
    async def test_send_session_command_input(self):
        proc, api = self._make_process()

        await proc.send_session_command_input("my-session", "cmd-1", "hello")

        send_request = api.send_input.call_args.kwargs["request"]
        assert send_request.data == "hello"


class TestSyncProcessSurface:
    def _make_process(self):
        from daytona._sync.process import Process

        mock_api = MagicMock()
        http_client = MagicMock()
        return Process("python", mock_api, http_client=http_client), mock_api, http_client

    def test_start_returns_serializable_handle_and_uses_request_payload(self):
        proc, api, _ = self._make_process()
        api.create_process.return_value = SimpleNamespace(id="prc-1")

        handle = proc.start(argv=["echo", "$HOME"], cwd="/tmp", env={"A": "1"}, name="demo", stdin="pipe")

        request = api.create_process.call_args.kwargs["request"]
        assert request.argv == ["echo", "$HOME"]
        assert request.shell_command is None
        assert request.cwd == "/tmp"
        assert request.env == {"A": "1"}
        assert request.name == "demo"
        assert request.stdin == "pipe"
        assert handle.id == "prc-1"

    def test_start_rejects_argv_and_shell_command_together(self):
        from daytona.common.errors import DaytonaValidationError

        proc, _, _ = self._make_process()

        with pytest.raises(DaytonaValidationError, match="exactly one of argv or shell_command"):
            proc.start(argv=["echo"], shell_command="echo hi")

    def test_run_waits_and_uses_on_exit_ttl_default(self):
        proc, api, _ = self._make_process()
        api.create_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)

        result = proc.run(shell_command="echo hi")

        request = api.create_process.call_args.kwargs["request"]
        assert request.keep_logs == "on_exit_ttl"
        assert api.wait_for_process.call_args.kwargs["id"] == "prc-1"
        assert result.exit_code == 0
        assert result.reason == "exited"

    def test_get_and_list_return_handles(self):
        proc, api, _ = self._make_process()
        api.get_process.return_value = SimpleNamespace(id="prc-1")
        api.list_processes.return_value = [SimpleNamespace(id="prc-1"), SimpleNamespace(id="prc-2")]

        handle = proc.get("prc-1")
        handles = proc.list(state="running")

        assert handle.id == "prc-1"
        assert [item.id for item in handles] == ["prc-1", "prc-2"]
        assert api.list_processes.call_args.kwargs["state"] == "running"

    def test_logs_raise_typed_cursor_expired_error(self):
        proc, api, _ = self._make_process()
        api.read_process_logs.side_effect = SyncToolboxApiException(
            status=409,
            reason="Conflict",
            body=(
                '{"message":"cursor expired","code":"CURSOR_EXPIRED","source":"DAYTONA_DAEMON",'
                '"statusCode":409,"path":"/processes/prc-1/logs","method":"GET",'
                '"timestamp":"2026-07-29T00:00:00Z"}'
            ),
        )
        handle = proc.get("prc-1")

        with pytest.raises(DaytonaProcessCursorExpiredError) as exc_info:
            handle.logs(cursor="cursor-1")

        assert exc_info.value.code == "CURSOR_EXPIRED"

    def test_stream_logs_yields_log_warning_state_and_eof_events(self):
        proc, api, http_client = self._make_process()
        api.get_process.return_value = SimpleNamespace(id="prc-1")
        api._read_process_logs_serialize.return_value = (
            "GET",
            "http://toolbox/processes/prc-1/logs?follow=true",
            {"Authorization": "Bearer token"},
            None,
            None,
        )
        http_client.stream.return_value = _FakeSyncStreamContext(
            _FakeSyncResponse(
                status_code=200,
                lines=[
                    "event: log",
                    'data: {"channel":"stdout","cursor":"cursor-1","data":"line-1\\n","encoding":"text","seq":1,"timestamp":"2026-07-29T00:00:00Z"}',
                    "",
                    "event: warning",
                    'data: {"cursor":"cursor-5","message":"frames before the first available cursor were evicted","firstAvailableCursor":"cursor-5"}',
                    "",
                    "event: state",
                    'data: {"id":"prc-1","createdAt":"2026-07-29T00:00:00Z","kind":"exec","state":"terminal","cwd":"/workspace","login":false,"stdin":"none","keepLogs":"until_cleanup","system":false,"truncatedHead":false,"cursor":"cursor-6"}',
                    "",
                    "event: eof",
                    'data: {"cursor":"cursor-6"}',
                    "",
                    "event: log",
                    'data: {"channel":"stdout","cursor":"cursor-7","data":"ignored","encoding":"text","seq":2,"timestamp":"2026-07-29T00:00:01Z"}',
                    "",
                ],
            )
        )

        handle = proc.get("prc-1")
        events = list(handle.stream_logs(cursor="cursor-0"))

        assert [event.type for event in events] == ["log", "warning", "state", "eof"]
        assert events[0].frame.data == "line-1\n"
        assert events[1].first_available_cursor == "cursor-5"
        assert events[2].process.state == "terminal"
        assert events[3].cursor == "cursor-6"
        http_client.stream.assert_called_once()

    def _arm_log_stream(self, api, http_client, response):
        api.create_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=None, reason="timed_out", signal=None)
        api._read_process_logs_serialize.return_value = (
            "GET",
            "http://toolbox/processes/prc-1/logs?follow=true",
            {"Authorization": "Bearer token"},
            None,
            None,
        )
        http_client.stream.return_value = _FakeSyncStreamContext(response)

    def test_run_with_callbacks_stops_streaming_when_wait_timeout_elapses(self):
        proc, api, http_client = self._make_process()
        self._arm_log_stream(
            api,
            http_client,
            _SilentSyncResponse(
                lines=[
                    "event: log",
                    'data: {"channel":"stdout","cursor":"cursor-1","data":"line-1\\n","encoding":"text","seq":1,"timestamp":"2026-07-29T00:00:00Z"}',
                    "",
                ]
            ),
        )
        collected: list[str] = []

        result = proc.run(shell_command="sleep 600", wait_timeout_ms=50, on_stdout=collected.append)

        assert http_client.stream.call_args.kwargs["timeout"] == 0.05
        assert api.wait_for_process.call_args.kwargs["timeout_ms"] == 1
        assert collected == ["line-1\n"]
        assert result.stdout == "line-1\n"
        assert result.reason == "timed_out"

    def test_run_with_callbacks_follows_stream_unbounded_without_wait_timeout(self):
        proc, api, http_client = self._make_process()
        self._arm_log_stream(
            api,
            http_client,
            _FakeSyncResponse(
                status_code=200,
                lines=[
                    "event: log",
                    'data: {"channel":"stderr","cursor":"cursor-1","data":"oops\\n","encoding":"text","seq":1,"timestamp":"2026-07-29T00:00:00Z"}',
                    "",
                    "event: eof",
                    'data: {"cursor":"cursor-2"}',
                    "",
                ],
            ),
        )
        collected: list[str] = []

        result = proc.run(shell_command="echo oops", on_stderr=collected.append)

        assert http_client.stream.call_args.kwargs["timeout"] is None
        assert api.wait_for_process.call_args.kwargs["timeout_ms"] is None
        assert collected == ["oops\n"]
        assert result.stderr == "oops\n"

    def test_handle_control_methods_delegate_to_process_endpoints(self):
        proc, api, _ = self._make_process()
        api.send_process_stdin.return_value = None
        api.signal_process.return_value = None
        api.resize_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)
        api.get_process.return_value = SimpleNamespace(id="prc-1")
        handle = proc.get("prc-1")

        handle.stdin(b"hello\n")
        handle.stdin_eof()
        handle.kill(escalate_after_ms=2000)
        handle.resize(120, 40)
        result = handle.wait(timeout_ms=500)

        stdin_request = api.send_process_stdin.call_args_list[0].kwargs["request"]
        eof_request = api.send_process_stdin.call_args_list[1].kwargs["request"]
        signal_request = api.signal_process.call_args.kwargs["request"]
        resize_request = api.resize_process.call_args.kwargs["request"]
        assert stdin_request.data == "hello\n"
        assert eof_request.eof is True
        assert signal_request.signal == "SIGTERM"
        assert signal_request.escalate_after_ms == 2000
        assert signal_request.escalate_to == "SIGKILL"
        assert resize_request.cols == 120
        assert resize_request.rows == 40
        assert result.reason == "exited"

    def test_attach_terminal_delegates_through_process_handle(self):
        proc, _, _ = self._make_process()
        proc._attach_process_terminal = MagicMock(return_value="attached-terminal")
        handle = proc.get("prc-1")

        assert handle.attach_terminal() == "attached-terminal"
        proc._attach_process_terminal.assert_called_once_with("prc-1")


class TestAsyncProcessSurface:
    def _make_process(self):
        from daytona._async.process import AsyncProcess

        mock_api = AsyncMock()
        mock_api.api_client = SimpleNamespace(http_session=MagicMock())
        return AsyncProcess("python", mock_api), mock_api

    @pytest.mark.asyncio
    async def test_start_and_run_support_handle_surface(self):
        proc, api = self._make_process()
        api.create_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)

        handle = await proc.start(argv=["echo", "$HOME"])
        result = await proc.run(shell_command="echo hi")

        request = api.create_process.call_args_list[0].kwargs["request"]
        assert request.argv == ["echo", "$HOME"]
        assert result.reason == "exited"

    @pytest.mark.asyncio
    async def test_logs_raise_typed_cursor_expired_error(self):
        proc, api = self._make_process()
        api.read_process_logs.side_effect = AsyncToolboxApiException(
            status=409,
            reason="Conflict",
            body=(
                '{"message":"cursor expired","code":"CURSOR_EXPIRED","source":"DAYTONA_DAEMON",'
                '"statusCode":409,"path":"/processes/prc-1/logs","method":"GET",'
                '"timestamp":"2026-07-29T00:00:00Z"}'
            ),
        )
        handle = await proc.get("prc-1")

        with pytest.raises(DaytonaProcessCursorExpiredError) as exc_info:
            await handle.logs(cursor="cursor-1")

    @pytest.mark.asyncio
    async def test_stream_logs_yields_all_event_kinds(self):
        proc, api = self._make_process()
        api.get_process.return_value = SimpleNamespace(id="prc-1")
        api._read_process_logs_serialize = MagicMock(
            return_value=(
                "GET",
                "http://toolbox/processes/prc-1/logs?follow=true",
                {"Authorization": "Bearer token"},
                None,
                None,
            )
        )
        http_session = api.api_client.http_session
        http_session.get.return_value = _FakeAsyncResponse(
            status=200,
            lines=[
                "event: log\n",
                'data: {"channel":"stdout","cursor":"cursor-1","data":"line-1\\n","encoding":"text","seq":1,"timestamp":"2026-07-29T00:00:00Z"}\n',
                "\n",
                "event: warning\n",
                'data: {"cursor":"cursor-5","message":"frames before the first available cursor were evicted","firstAvailableCursor":"cursor-5"}\n',
                "\n",
                "event: state\n",
                'data: {"id":"prc-1","createdAt":"2026-07-29T00:00:00Z","kind":"exec","state":"terminal","cwd":"/workspace","login":false,"stdin":"none","keepLogs":"until_cleanup","system":false,"truncatedHead":false,"cursor":"cursor-6"}\n',
                "\n",
                "event: eof\n",
                'data: {"cursor":"cursor-6"}\n',
                "\n",
            ],
        )
        handle = await proc.get("prc-1")

        events = [event async for event in handle.stream_logs(cursor="cursor-0")]

        assert [event.type for event in events] == ["log", "warning", "state", "eof"]
        assert events[0].frame.data == "line-1\n"
        assert events[2].process.state == "terminal"

    @pytest.mark.asyncio
    async def test_run_with_callbacks_stops_streaming_when_wait_timeout_elapses(self):
        proc, api = self._make_process()
        api.create_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=None, reason="timed_out", signal=None)
        api._read_process_logs_serialize = MagicMock(
            return_value=(
                "GET",
                "http://toolbox/processes/prc-1/logs?follow=true",
                {"Authorization": "Bearer token"},
                None,
                None,
            )
        )
        response = _HangingAsyncResponse(
            lines=[
                "event: log\n",
                'data: {"channel":"stdout","cursor":"cursor-1","data":"line-1\\n","encoding":"text","seq":1,"timestamp":"2026-07-29T00:00:00Z"}\n',
                "\n",
            ]
        )
        api.api_client.http_session.get.return_value = response
        collected: list[str] = []

        started = time.monotonic()
        result = await asyncio.wait_for(
            proc.run(shell_command="sleep 600", wait_timeout_ms=50, on_stdout=collected.append),
            timeout=5,
        )

        assert time.monotonic() - started < 5
        assert api.wait_for_process.call_args.kwargs["timeout_ms"] == 1
        assert collected == ["line-1\n"]
        assert result.stdout == "line-1\n"
        assert result.reason == "timed_out"
        assert response.closed is True

    @pytest.mark.asyncio
    async def test_run_with_callbacks_follows_stream_unbounded_without_wait_timeout(self):
        proc, api = self._make_process()
        api.create_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)
        api._read_process_logs_serialize = MagicMock(
            return_value=(
                "GET",
                "http://toolbox/processes/prc-1/logs?follow=true",
                {"Authorization": "Bearer token"},
                None,
                None,
            )
        )
        api.api_client.http_session.get.return_value = _FakeAsyncResponse(
            status=200,
            lines=[
                "event: log\n",
                'data: {"channel":"stderr","cursor":"cursor-1","data":"oops\\n","encoding":"text","seq":1,"timestamp":"2026-07-29T00:00:00Z"}\n',
                "\n",
                "event: eof\n",
                'data: {"cursor":"cursor-2"}\n',
                "\n",
            ],
        )
        collected: list[str] = []

        result = await proc.run(shell_command="echo oops", on_stderr=collected.append)

        assert api.wait_for_process.call_args.kwargs["timeout_ms"] is None
        assert collected == ["oops\n"]
        assert result.stderr == "oops\n"

    @pytest.mark.asyncio
    async def test_handle_control_methods_delegate_to_process_endpoints(self):
        proc, api = self._make_process()
        api.send_process_stdin.return_value = None
        api.signal_process.return_value = None
        api.resize_process.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)
        handle = await proc.get("prc-1")

        await handle.stdin("hello\n")
        await handle.stdin_eof()
        await handle.kill(escalate_after_ms=2000)
        await handle.resize(120, 40)
        result = await handle.wait(timeout_ms=500)

        stdin_request = api.send_process_stdin.call_args_list[0].kwargs["request"]
        eof_request = api.send_process_stdin.call_args_list[1].kwargs["request"]
        signal_request = api.signal_process.call_args.kwargs["request"]
        resize_request = api.resize_process.call_args.kwargs["request"]
        assert stdin_request.data == "hello\n"
        assert eof_request.eof is True
        assert signal_request.signal == "SIGTERM"
        assert signal_request.escalate_after_ms == 2000
        assert resize_request.cols == 120
        assert result.reason == "exited"

    @pytest.mark.asyncio
    async def test_attach_terminal_delegates_through_process_handle(self):
        proc, _ = self._make_process()
        proc._attach_process_terminal = AsyncMock(return_value="attached-terminal")
        handle = await proc.get("prc-1")

        assert await handle.attach_terminal() == "attached-terminal"
        proc._attach_process_terminal.assert_awaited_once_with("prc-1")


class TestProcessRunOutputAggregation:
    """run() must return aggregated stdout/stderr alongside exit metadata."""

    def test_decode_run_frame_routes_channels(self):
        from daytona.common.process import decode_run_frame

        assert decode_run_frame("stdout", "out", "text") == ("stdout", "out")
        assert decode_run_frame("stderr", "err", "text") == ("stderr", "err")
        # A pty merges streams by construction; its frames count as stdout.
        assert decode_run_frame("pty", "p", "text") == ("stdout", "p")
        # System frames are daemon provenance, not process output.
        assert decode_run_frame("system", "fork", "text") == (None, "fork")

    def test_decode_run_frame_decodes_base64(self):
        import base64

        from daytona.common.process import decode_run_frame

        encoded = base64.b64encode("hellò".encode()).decode()
        assert decode_run_frame("stdout", encoded, "base64") == ("stdout", "hellò")


class TestRunFrameDecoder:
    def test_reassembles_multibyte_codepoint_split_across_frames(self):
        from daytona.common.process import RunFrameDecoder

        decoder = RunFrameDecoder()
        side1, data1 = decoder.decode("stdout", base64.b64encode(b"\xf0\x9f").decode(), "base64")
        side2, data2 = decoder.decode("stdout", base64.b64encode(b"\x98\x80 ok\n").decode(), "base64")
        assert (side1, data1) == ("stdout", "")
        assert (side2, data2) == ("stdout", "\U0001f600 ok\n")
        assert decoder.flush() == ("", "")

    def test_flush_replaces_dangling_trailing_bytes(self):
        from daytona.common.process import RunFrameDecoder

        decoder = RunFrameDecoder()
        _, data = decoder.decode("stderr", base64.b64encode(b"ok\xf0").decode(), "base64")
        assert data == "ok"
        assert decoder.flush() == ("", "\ufffd")

    def test_channels_decode_independently(self):
        from daytona.common.process import RunFrameDecoder

        decoder = RunFrameDecoder()
        decoder.decode("stdout", base64.b64encode(b"\xf0\x9f").decode(), "base64")
        _, err = decoder.decode("stderr", base64.b64encode(b"\xe2\x9c\x85").decode(), "base64")
        assert err == "\u2705"
        _, out = decoder.decode("stdout", base64.b64encode(b"\x98\x80").decode(), "base64")
        assert out == "\U0001f600"


class TestHandleOutputAndConnect:
    def test_output_combines_record_metadata_with_collected_logs(self):
        from daytona.handle.process_handle import ProcessHandle

        client = MagicMock()
        client.sandbox_id = "sbx-1"
        client._collect_run_output.return_value = ("hello\n", "warn\n")
        client._get_process_record.return_value = SimpleNamespace(
            exit_code=3, signal=None, reason="exited", state="terminal"
        )
        handle = ProcessHandle(client, "prc_out")

        result = handle.output()

        assert (result.stdout, result.stderr) == ("hello\n", "warn\n")
        assert (result.exit_code, result.reason, result.id) == (3, "exited", "prc_out")
        client._collect_run_output.assert_called_once_with(handle)

    def test_output_falls_back_to_state_while_running(self):
        from daytona.handle.process_handle import ProcessHandle

        client = MagicMock()
        client._collect_run_output.return_value = ("partial", "")
        client._get_process_record.return_value = SimpleNamespace(
            exit_code=None, signal=None, reason=None, state="running"
        )

        result = ProcessHandle(client, "prc_live").output()

        assert result.reason == "running"
        assert result.exit_code is None

    def test_connect_delegates_to_get(self):
        from daytona._sync.process import Process

        process = MagicMock(spec=Process)
        process.connect = Process.connect.__wrapped__.__get__(process)
        handle = object()
        process.get.return_value = handle

        assert process.connect("prc_x") is handle
        process.get.assert_called_once_with("prc_x")

    def test_cleanup_delegates_to_client(self):
        from daytona.handle.process_handle import ProcessHandle

        client = MagicMock()
        ProcessHandle(client, "prc_gone").cleanup()
        client._cleanup_process.assert_called_once_with("prc_gone")
