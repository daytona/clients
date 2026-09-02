# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

import pytest

from daytona import SessionExecuteRequest
from daytona.common.errors import DaytonaProcessNotFoundError, DaytonaSessionEndedError

from ._helpers import MUX_STDERR, MUX_STDOUT, unique_name, wait_until_async

pytestmark = [pytest.mark.e2e, pytest.mark.golden, pytest.mark.asyncio(loop_scope="module")]


async def test_async_session_create_get_list_and_delete_shape(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)

    session = await async_sandbox.process.get_session(session_id)
    listed = await async_sandbox.process.list_sessions()
    await async_sandbox.process.delete_session(session_id)

    assert session.session_id == session_id
    assert session.commands == []
    assert any(item.session_id == session_id for item in listed)

    with pytest.raises(DaytonaProcessNotFoundError) as exc_info:
        await async_sandbox.process.get_session(session_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "PROCESS_NOT_FOUND"


async def test_async_session_logs_snapshot_and_follow_pin_current_buffering(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)
    command = (
        'printf "out1\\n"; sleep 0.2; printf "err1\\n" >&2; sleep 2; '
        'printf "out2\\n"; sleep 0.2; printf "err2\\n" >&2'
    )
    response = await async_sandbox.process.execute_session_command(
        session_id,
        SessionExecuteRequest(command=command, run_async=True),
    )

    await asyncio.sleep(0.5)
    snapshot = await async_sandbox.process.get_session_command_logs(session_id, response.cmd_id)
    streamed_stdout: list[str] = []
    streamed_stderr: list[str] = []
    await async_sandbox.process.get_session_command_logs_async(
        session_id,
        response.cmd_id,
        lambda chunk: streamed_stdout.append(chunk),
        lambda chunk: streamed_stderr.append(chunk),
    )
    final_logs = await async_sandbox.process.get_session_command_logs(session_id, response.cmd_id)

    assert response.output is None
    assert response.exit_code is None
    assert response.stdout == ""
    assert response.stderr == ""
    assert snapshot.output == f"{MUX_STDOUT}out1\n{MUX_STDERR}err1\n"
    assert snapshot.stdout == "out1\n"
    assert snapshot.stderr == "err1\n"
    assert streamed_stdout == ["out1\n", "out2\n"]
    assert streamed_stderr == ["err1\n", "err2\n"]
    assert final_logs.output == f"{MUX_STDOUT}out1\n{MUX_STDERR}err1\n{MUX_STDOUT}out2\n{MUX_STDERR}err2\n"
    assert final_logs.stdout == "out1\nout2\n"
    assert final_logs.stderr == "err1\nerr2\n"


async def test_async_session_get_session_command_exit_code_transitions(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)
    command = 'printf "out\\n"; sleep 1; printf "err\\n" >&2'
    response = await async_sandbox.process.execute_session_command(
        session_id,
        SessionExecuteRequest(command=command, run_async=True),
    )
    immediate = await async_sandbox.process.get_session_command(session_id, response.cmd_id)

    async def command_finished() -> bool:
        command_state = await async_sandbox.process.get_session_command(session_id, response.cmd_id)
        return command_state.exit_code == 0

    await wait_until_async(command_finished)
    finished = await async_sandbox.process.get_session_command(session_id, response.cmd_id)

    assert immediate.id == response.cmd_id
    assert immediate.command == command
    assert immediate.exit_code is None
    assert finished.exit_code == 0


async def test_async_session_execute_sync_response_shape(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)

    response = await async_sandbox.process.execute_session_command(
        session_id,
        SessionExecuteRequest(command='printf "sync1\\n"; sleep 0.2; printf "synce\\n" >&2'),
    )

    assert response.exit_code == 0
    assert response.output == f"{MUX_STDOUT}sync1\n{MUX_STDERR}synce\n"
    assert response.stdout == "sync1\n"
    assert response.stderr == "synce\n"


async def test_async_session_cwd_and_env_are_currently_ignored_by_daemon(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)

    response = await async_sandbox.process.execute_session_command(
        session_id,
        SessionExecuteRequest(command="pwd; echo $A", cwd="/tmp", env={"A": "1"}),
    )

    assert response.exit_code == 0
    assert response.output == f"{MUX_STDOUT}/home/daytona\n{MUX_STDOUT}\n"
    assert response.stdout == "/home/daytona\n\n"
    assert response.stderr == ""


async def test_async_send_session_command_input_echoes_via_data_field(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)
    response = await async_sandbox.process.execute_session_command(
        session_id,
        SessionExecuteRequest(command="cat", run_async=True),
    )

    await asyncio.sleep(1)
    await async_sandbox.process.send_session_command_input(session_id, response.cmd_id, "hello-stdin\n")
    await asyncio.sleep(1)
    logs = await async_sandbox.process.get_session_command_logs(session_id, response.cmd_id)

    assert logs.output == f"{MUX_STDOUT}hello-stdin\n{MUX_STDOUT}hello-stdin\n"
    assert logs.stdout == "hello-stdin\nhello-stdin\n"
    assert logs.stderr == ""


async def test_async_session_exit_terminates_shell_and_next_execute_fails(async_sandbox) -> None:
    session_id = unique_name("golden-session")
    await async_sandbox.process.create_session(session_id)
    response = await async_sandbox.process.execute_session_command(
        session_id,
        SessionExecuteRequest(command='printf "bye\\n"; exit 7', run_async=True),
    )

    await asyncio.sleep(1)
    logs = await async_sandbox.process.get_session_command_logs(session_id, response.cmd_id)

    with pytest.raises(DaytonaSessionEndedError) as exc_info:
        await async_sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(command='printf "after\\n"'),
            timeout=5,
        )

    assert logs.output == f"{MUX_STDOUT}bye\n"
    assert logs.stdout == "bye\n"
    assert logs.stderr == ""
    assert exc_info.value.status_code == 410
    assert exc_info.value.code == "SESSION_ENDED"


async def test_async_entrypoint_session_and_logs_shape(async_sandbox) -> None:
    session = await async_sandbox.process.get_entrypoint_session()
    logs = await async_sandbox.process.get_entrypoint_logs()

    assert session.session_id == "entrypoint"
    assert len(session.commands) == 1
    assert session.commands[0].id == "entrypoint_command"
    assert session.commands[0].command == "'sleep' 'infinity'"
    assert logs.output == ""
    assert logs.stdout == ""
    assert logs.stderr == ""
