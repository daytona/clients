# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona.common.errors import DaytonaDaemonUpgradeRequiredError, DaytonaProcessCursorExpiredError
from daytona.common.process import CodeRunParams
from daytona_toolbox_api_client.exceptions import ApiException as SyncToolboxApiException
from daytona_toolbox_api_client_async.exceptions import ApiException as AsyncToolboxApiException
from daytona_toolbox_api_client import Chart as GeneratedChart


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


class TestSyncProcessV2:
    def _make_process(self):
        from daytona._sync.process import Process

        mock_api = MagicMock()
        http_client = MagicMock()
        return Process("python", mock_api, http_client=http_client, sandbox_id="sandbox-1"), mock_api, http_client

    def test_start_returns_serializable_handle_and_uses_request_payload(self):
        proc, api, _ = self._make_process()
        api.create_process_v2.return_value = SimpleNamespace(id="prc-1")

        handle = proc.start(argv=["echo", "$HOME"], cwd="/tmp", env={"A": "1"}, name="demo", stdin="pipe")

        request = api.create_process_v2.call_args.kwargs["request"]
        assert request.argv == ["echo", "$HOME"]
        assert request.shell_command is None
        assert request.cwd == "/tmp"
        assert request.env == {"A": "1"}
        assert request.name == "demo"
        assert request.stdin == "pipe"
        assert handle.id == "prc-1"
        assert handle.to_json() == {"sandboxId": "sandbox-1", "processId": "prc-1"}

    def test_start_rejects_argv_and_shell_command_together(self):
        from daytona.common.errors import DaytonaValidationError

        proc, _, _ = self._make_process()

        with pytest.raises(DaytonaValidationError, match="exactly one of argv or shell_command"):
            proc.start(argv=["echo"], shell_command="echo hi")

    def test_run_waits_and_uses_on_exit_ttl_default(self):
        proc, api, _ = self._make_process()
        api.create_process_v2.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process_v2.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)

        result = proc.run(shell_command="echo hi")

        request = api.create_process_v2.call_args.kwargs["request"]
        assert request.keep_logs == "on_exit_ttl"
        assert api.wait_for_process_v2.call_args.kwargs["id"] == "prc-1"
        assert result.exit_code == 0
        assert result.reason == "exited"

    def test_get_and_list_return_handles(self):
        proc, api, _ = self._make_process()
        api.get_process_v2.return_value = SimpleNamespace(id="prc-1")
        api.list_processes_v2.return_value = [SimpleNamespace(id="prc-1"), SimpleNamespace(id="prc-2")]

        handle = proc.get("prc-1")
        handles = proc.list(state="running")

        assert handle.id == "prc-1"
        assert [item.id for item in handles] == ["prc-1", "prc-2"]
        assert api.list_processes_v2.call_args.kwargs["state"] == "running"

    def test_from_json_requires_matching_sandbox(self):
        from daytona.common.errors import DaytonaValidationError

        proc, _, _ = self._make_process()

        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})
        assert handle.id == "prc-1"

        with pytest.raises(DaytonaValidationError, match="belongs to sandbox"):
            proc.from_json({"sandboxId": "sandbox-2", "processId": "prc-1"})

    def test_logs_raise_typed_cursor_expired_error(self):
        proc, api, _ = self._make_process()
        api.get_process_logs_v2.side_effect = SyncToolboxApiException(
            status=409,
            reason="Conflict",
            body=(
                '{"message":"cursor expired","code":"CURSOR_EXPIRED","source":"DAYTONA_DAEMON",'
                '"statusCode":409,"path":"/process/v2/processes/prc-1/logs","method":"GET",'
                '"timestamp":"2026-07-29T00:00:00Z","details":{"firstAvailableCursor":"cursor-5"}}'
            ),
        )
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        with pytest.raises(DaytonaProcessCursorExpiredError) as exc_info:
            handle.logs(cursor="cursor-1")

        assert exc_info.value.first_available_cursor == "cursor-5"
        assert exc_info.value.code == "CURSOR_EXPIRED"

    def test_stream_logs_yields_log_warning_state_and_eof_events(self):
        proc, api, http_client = self._make_process()
        api.get_process_v2.return_value = SimpleNamespace(id="prc-1")
        api._get_process_logs_v2_serialize.return_value = (
            "GET",
            "http://toolbox/process/v2/processes/prc-1/logs?follow=true",
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

        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})
        events = list(handle.stream_logs(cursor="cursor-0"))

        assert [event.type for event in events] == ["log", "warning", "state", "eof"]
        assert events[0].frame.data == "line-1\n"
        assert events[1].first_available_cursor == "cursor-5"
        assert events[2].process.state == "terminal"
        assert events[3].cursor == "cursor-6"
        http_client.stream.assert_called_once()

    def test_v2_methods_cache_upgrade_required_per_sandbox(self):
        proc, api, _ = self._make_process()
        api.get_process_v2.side_effect = SyncToolboxApiException(
            status=404, reason="Not Found", body="404 page not found"
        )

        with pytest.raises(DaytonaDaemonUpgradeRequiredError):
            proc.get("prc-1")

        with pytest.raises(DaytonaDaemonUpgradeRequiredError):
            proc.start(shell_command="echo hi")

        api.create_process_v2.assert_not_called()

    def test_handle_control_methods_delegate_to_v2_endpoints(self):
        proc, api, _ = self._make_process()
        api.send_process_stdin_v2.return_value = None
        api.signal_process_v2.return_value = None
        api.resize_process_v2.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process_v2.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)
        api.get_process_v2.return_value = SimpleNamespace(id="prc-1")
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        handle.stdin(b"hello\n")
        handle.stdin_eof()
        handle.kill(escalate_after_ms=2000)
        handle.resize(120, 40)
        result = handle.wait(timeout_ms=500)

        stdin_request = api.send_process_stdin_v2.call_args_list[0].kwargs["request"]
        eof_request = api.send_process_stdin_v2.call_args_list[1].kwargs["request"]
        signal_request = api.signal_process_v2.call_args.kwargs["request"]
        resize_request = api.resize_process_v2.call_args.kwargs["request"]
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
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        assert handle.attach_terminal() == "attached-terminal"
        proc._attach_process_terminal.assert_called_once_with("prc-1")


class TestAsyncProcessV2:
    def _make_process(self):
        from daytona._async.process import AsyncProcess

        mock_api = AsyncMock()
        mock_api.api_client = SimpleNamespace(http_session=MagicMock())
        return AsyncProcess("python", mock_api, sandbox_id="sandbox-1"), mock_api

    @pytest.mark.asyncio
    async def test_start_and_run_support_v2_handle_surface(self):
        proc, api = self._make_process()
        api.create_process_v2.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process_v2.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)

        handle = await proc.start(argv=["echo", "$HOME"])
        result = await proc.run(shell_command="echo hi")

        request = api.create_process_v2.call_args_list[0].kwargs["request"]
        assert request.argv == ["echo", "$HOME"]
        assert handle.to_json() == {"sandboxId": "sandbox-1", "processId": "prc-1"}
        assert result.reason == "exited"

    @pytest.mark.asyncio
    async def test_logs_raise_typed_cursor_expired_error(self):
        proc, api = self._make_process()
        api.get_process_logs_v2.side_effect = AsyncToolboxApiException(
            status=409,
            reason="Conflict",
            body=(
                '{"message":"cursor expired","code":"CURSOR_EXPIRED","source":"DAYTONA_DAEMON",'
                '"statusCode":409,"path":"/process/v2/processes/prc-1/logs","method":"GET",'
                '"timestamp":"2026-07-29T00:00:00Z","details":{"firstAvailableCursor":"cursor-5"}}'
            ),
        )
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        with pytest.raises(DaytonaProcessCursorExpiredError) as exc_info:
            await handle.logs(cursor="cursor-1")

        assert exc_info.value.first_available_cursor == "cursor-5"

    @pytest.mark.asyncio
    async def test_stream_logs_yields_all_event_kinds(self):
        proc, api = self._make_process()
        api.get_process_v2.return_value = SimpleNamespace(id="prc-1")
        api._get_process_logs_v2_serialize = MagicMock(
            return_value=(
                "GET",
                "http://toolbox/process/v2/processes/prc-1/logs?follow=true",
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
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        events = [event async for event in handle.stream_logs(cursor="cursor-0")]

        assert [event.type for event in events] == ["log", "warning", "state", "eof"]
        assert events[0].frame.data == "line-1\n"
        assert events[2].process.state == "terminal"

    @pytest.mark.asyncio
    async def test_v2_methods_cache_upgrade_required_per_sandbox(self):
        proc, api = self._make_process()
        api.get_process_v2.side_effect = AsyncToolboxApiException(
            status=404, reason="Not Found", body="404 page not found"
        )

        with pytest.raises(DaytonaDaemonUpgradeRequiredError):
            await proc.get("prc-1")

        with pytest.raises(DaytonaDaemonUpgradeRequiredError):
            await proc.start(shell_command="echo hi")

        api.create_process_v2.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_control_methods_delegate_to_v2_endpoints(self):
        proc, api = self._make_process()
        api.send_process_stdin_v2.return_value = None
        api.signal_process_v2.return_value = None
        api.resize_process_v2.return_value = SimpleNamespace(id="prc-1")
        api.wait_for_process_v2.return_value = SimpleNamespace(exit_code=0, reason="exited", signal=None)
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        await handle.stdin("hello\n")
        await handle.stdin_eof()
        await handle.kill(escalate_after_ms=2000)
        await handle.resize(120, 40)
        result = await handle.wait(timeout_ms=500)

        stdin_request = api.send_process_stdin_v2.call_args_list[0].kwargs["request"]
        eof_request = api.send_process_stdin_v2.call_args_list[1].kwargs["request"]
        signal_request = api.signal_process_v2.call_args.kwargs["request"]
        resize_request = api.resize_process_v2.call_args.kwargs["request"]
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
        handle = proc.from_json({"sandboxId": "sandbox-1", "processId": "prc-1"})

        assert await handle.attach_terminal() == "attached-terminal"
        proc._attach_process_terminal.assert_awaited_once_with("prc-1")
