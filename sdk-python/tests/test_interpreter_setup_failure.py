# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A code run whose kernel could not be set up raises; it never looks like a run."""

from __future__ import annotations

from typing import Optional

import pytest

from daytona.v2 import KernelUnavailableError, ServiceUnavailableError
from daytona.v2._core import RequestSpec
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.process import AsyncProcessService

INSTALL_FAILURE = (
    "cannot install ipykernel (is PyPI reachable from this sandbox?): "
    "uv pip install: exit 1: network unreachable"
)


class _Transport:
    request_timeout_seconds = 30.0
    max_response_bytes = 0

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec) -> object:
        self.specs.append(spec)
        return self.responses.pop(0)


def _process(process_id: str = "proc-1") -> dict[str, object]:
    return {
        "id": process_id,
        "kind": "code",
        "state": "running",
        "createdAt": "2026-01-01T00:00:00Z",
        "cwd": "/workspace",
        "stdin": "closed",
        "keepLogs": "on_exit_ttl",
        "truncatedHead": False,
        "language": "python",
    }


def _frame(cursor: str, channel: str, data: str) -> dict[str, object]:
    return {"cursor": cursor, "channel": channel, "data": data, "timestamp": 1}


def _page(frames: list[dict[str, object]]) -> dict[str, object]:
    return {"frames": frames, "nextCursor": str(len(frames)), "eof": True, "truncatedHead": False}


def _service(responses: list[object]) -> tuple[AsyncInterpreterService, _Transport]:
    transport = _Transport(responses)
    process = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]
    return AsyncInterpreterService("sbx", transport, process), transport  # type: ignore[arg-type]


def _setup_failed_run(frames: Optional[list[dict[str, object]]] = None) -> list[object]:
    if frames is None:
        frames = [
            _frame("1", "setup", "Preparing the python kernel (first use): installing ipykernel\n"),
            _frame("2", "setup", f"code run failed: {INSTALL_FAILURE}\n"),
        ]
    return [_process(), {"reason": "failed"}, _page(frames)]


@pytest.mark.anyio
async def test_a_run_whose_kernel_setup_failed_raises_kernel_unavailable_with_the_diagnostic() -> None:
    interpreter, _ = _service(_setup_failed_run())

    with pytest.raises(KernelUnavailableError) as raised:
        await interpreter.run("print(1)")

    error = raised.value
    assert isinstance(error, ServiceUnavailableError)
    assert error.status_code == 503
    assert error.code == "KERNEL_UNAVAILABLE"
    assert error.source == "SDK"
    assert INSTALL_FAILURE in error.message
    assert "proc-1" in error.message
    assert "Preparing the python kernel" in error.message


@pytest.mark.anyio
async def test_a_setup_failure_without_a_diagnostic_still_raises() -> None:
    interpreter, _ = _service(_setup_failed_run(frames=[]))

    with pytest.raises(KernelUnavailableError) as raised:
        await interpreter.run("print(1)", language="typescript")

    assert "no setup diagnostic" in raised.value.message


@pytest.mark.anyio
async def test_explicit_and_isolated_contexts_raise_the_same_error() -> None:
    interpreter, _ = _service(_setup_failed_run())
    context = interpreter.connect_context("ctx-1", language="python")
    with pytest.raises(KernelUnavailableError):
        await context.run("print(1)")

    session = {"id": "isolated-1", "kernel": "python", "active": True, "createdAt": "2026-01-01T00:00:00Z"}
    interpreter, transport = _service([session, *_setup_failed_run(), None])
    with pytest.raises(KernelUnavailableError):
        await interpreter.run("print(1)", isolated=True)
    # The throwaway context is still deleted.
    assert transport.specs[-1].method == "DELETE"


@pytest.mark.anyio
async def test_setup_narration_of_a_successful_run_stays_out_of_the_result() -> None:
    interpreter, _ = _service(
        [
            _process(),
            {"reason": "exited", "exitCode": 0},
            _page(
                [
                    _frame("1", "setup", "kernel is gone; restarting it (session state is lost)\n"),
                    _frame("2", "stdout", "1\n"),
                ]
            ),
        ]
    )

    result = await interpreter.run("print(1)")

    assert result.process.reason == "exited"
    assert result.logs.stdout == "1\n"
    assert result.error is None


@pytest.mark.anyio
async def test_a_kernel_lost_mid_run_returns_its_partial_result() -> None:
    interpreter, _ = _service(
        [
            _process(),
            {"reason": "lost"},
            _page(
                [
                    _frame("1", "stdout", "partial\n"),
                    _frame("2", "setup", "kernel died mid-execution: connection closed\n"),
                ]
            ),
        ]
    )

    result = await interpreter.run("print(1)")

    assert result.process.reason == "lost"
    assert result.logs.stdout == "partial\n"


def test_the_sync_client_raises_the_same_error() -> None:
    from daytona.v2._bridge import SyncBridge
    from daytona.v2._sync.interpreter import InterpreterService

    async_service, _ = _service(_setup_failed_run())
    bridge = SyncBridge(name="InterpreterService")
    try:
        with pytest.raises(KernelUnavailableError) as raised:
            InterpreterService(async_service, bridge).run("print(1)")  # type: ignore[arg-type]
    finally:
        bridge.close()

    assert INSTALL_FAILURE in raised.value.message
