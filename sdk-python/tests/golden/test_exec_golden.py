# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from daytona.common.errors import DaytonaBadRequestError, DaytonaProcessExecutionTimeoutError

from ._helpers import assert_daemon_error

pytestmark = [pytest.mark.e2e, pytest.mark.golden]


def test_process_exec_combines_streams_in_emission_order(sandbox) -> None:
    result = sandbox.process.exec(
        'printf "out1\\n"; sleep 0.1; printf "err1\\n" >&2; sleep 0.1; printf "out2\\n"; exit 3'
    )

    assert result.exit_code == 3
    assert result.result == "out1\nerr1\nout2\n"
    assert result.artifacts is not None
    assert result.artifacts.stdout == "out1\nerr1\nout2\n"


def test_process_exec_honors_cwd_and_env(sandbox) -> None:
    result = sandbox.process.exec("pwd; echo $FOO", cwd="/tmp", env={"FOO": "bar"})

    assert result.exit_code == 0
    assert result.result == "/tmp\nbar\n"


def test_process_exec_uses_zsh_expansion(sandbox) -> None:
    result = sandbox.process.exec("echo $HOME")

    assert result.exit_code == 0
    assert result.result == "/home/daytona\n"


def test_process_exec_missing_binary_returns_127(sandbox) -> None:
    result = sandbox.process.exec("definitely-not-a-binary-xyz")

    # Exit code 127 is the contract; the wording belongs to whichever shell the
    # daemon's legacy zsh -> bash -> sh discovery resolved, which depends on the image.
    assert result.exit_code == 127
    assert "definitely-not-a-binary-xyz" in result.result
    assert "command not found" in result.result or "not found" in result.result


def test_process_exec_has_no_default_timeout_in_current_python_sdk(sandbox) -> None:
    started = time.monotonic()
    result = sandbox.process.exec('printf "start\\n"; sleep 15; printf "end\\n"')
    elapsed = time.monotonic() - started

    assert elapsed >= 14.0
    assert result.exit_code == 0
    assert result.result == "start\nend\n"


def test_process_exec_explicit_timeout_maps_daemon_error(sandbox) -> None:
    with pytest.raises(DaytonaProcessExecutionTimeoutError) as exc_info:
        sandbox.process.exec('printf "start\\n"; sleep 10; printf "end\\n"', timeout=2)

    assert_daemon_error(
        exc_info.value,
        status_code=408,
        code="PROCESS_EXECUTION_TIMEOUT",
        message="Failed to execute command: command execution timeout",
    )


def test_process_exec_empty_command_raises_invalid_request_body(sandbox) -> None:
    with pytest.raises(DaytonaBadRequestError) as exc_info:
        sandbox.process.exec("")

    assert_daemon_error(
        exc_info.value,
        status_code=400,
        code="INVALID_REQUEST_BODY",
        message=(
            "Failed to execute command: invalid body request: invalid request body: "
            "Key: 'ExecuteRequest.Command' Error:Field validation for 'Command' failed on the 'required' tag"
        ),
    )
