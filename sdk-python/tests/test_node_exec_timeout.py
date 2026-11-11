# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Execution deadlines for the synchronous and asynchronous all-node helpers."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona._async.sandbox import AsyncSandboxNodeCollection
from daytona._sync.sandbox import SandboxNodeCollection
from daytona.common.errors import DaytonaError, DaytonaTimeoutError, DaytonaValidationError
from daytona.common.process import ExecuteResponse, ExecutionArtifacts


def make_nodes(kind, count=1):
    collection = SandboxNodeCollection(None) if kind == "sync" else AsyncSandboxNodeCollection(None)
    mock = MagicMock if kind == "sync" else AsyncMock
    response = ExecuteResponse.model_construct(
        exit_code=0, result="done", artifacts=ExecutionArtifacts(stdout="done", charts=[])
    )
    nodes = []
    for rank in range(count):
        process = SimpleNamespace(
            exec=mock(return_value=response),
            create_session=mock(),
            execute_session_command=mock(return_value=SimpleNamespace(cmd_id=f"command-{rank}")),
            get_session_command_logs_async=AsyncMock(),
            get_session_command=mock(return_value=SimpleNamespace(exit_code=0)),
            get_session_command_logs=mock(return_value=SimpleNamespace(output="done", stdout="done", stderr="")),
            delete_session=mock(),
        )
        nodes.append(SimpleNamespace(rank=rank, warnings=[], process=process))
    collection._replace(nodes)
    return collection, nodes


async def invoke(kind, collection, **kwargs):
    if kind == "sync":
        return await asyncio.to_thread(collection.process.exec, "workload", **kwargs)
    return await collection.process.exec("workload", **kwargs)


async def never_finishes(*_args):
    await asyncio.Future()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("timeout", [None, 0, 15])
async def test_buffered_exec_forwards_each_rank_timeout(kind, timeout):
    collection, nodes = make_nodes(kind, count=2)

    results = await invoke(kind, collection, timeout=timeout)

    assert list(results) == [0, 1]
    for node in nodes:
        if timeout is None:
            node.process.exec.assert_any_call("workload")
        else:
            node.process.exec.assert_any_call("workload", timeout=timeout)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("timeout", [-1, 1.5, float("nan"), float("inf"), True])
async def test_invalid_timeout_is_rejected_before_dispatch(kind, timeout):
    collection, nodes = make_nodes(kind)

    with pytest.raises(DaytonaValidationError, match="timeout"):
        await invoke(kind, collection, timeout=timeout)

    nodes[0].process.exec.assert_not_called()
    nodes[0].process.create_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("timeout", [None, 0, 10])
async def test_stream_completion_cleans_up_its_session(kind, timeout):
    collection, nodes = make_nodes(kind)

    results = await invoke(kind, collection, timeout=timeout, on_stdout=lambda _chunk: None)

    assert results[0].result == "done"
    session_id = nodes[0].process.create_session.call_args.args[0]
    if timeout:
        nodes[0].process.delete_session.assert_called_once_with(session_id, request_timeout=10)
    else:
        nodes[0].process.delete_session.assert_called_once_with(session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_stream_timeout_cancels_log_wait_and_deletes_owned_session(kind):
    collection, nodes = make_nodes(kind)
    cancelled = []

    async def stream(*_args):
        try:
            await never_finishes()
        finally:
            cancelled.append(True)

    nodes[0].process.get_session_command_logs_async.side_effect = stream

    with pytest.raises(DaytonaTimeoutError, match="rank 0"):
        await invoke(kind, collection, timeout=1, on_stdout=lambda _chunk: None)

    assert cancelled == [True]
    session_id = nodes[0].process.create_session.call_args.args[0]
    nodes[0].process.delete_session.assert_called_once_with(session_id, request_timeout=10)
    nodes[0].process.get_session_command.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_cleanup_failure_preserves_timeout_and_exposes_cleanup_error(kind):
    collection, nodes = make_nodes(kind)
    nodes[0].process.get_session_command_logs_async.side_effect = never_finishes
    cleanup_error = DaytonaError("session cleanup failed")
    nodes[0].process.delete_session.side_effect = cleanup_error

    with pytest.raises(DaytonaTimeoutError) as error:
        await invoke(kind, collection, timeout=1, on_stderr=lambda _chunk: None)

    assert error.value.__cause__ is cleanup_error


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_session_start_failure_is_preserved_when_cleanup_also_fails(kind):
    collection, nodes = make_nodes(kind)
    original = DaytonaError("command start failed")
    cleanup_error = DaytonaError("cleanup failed")
    nodes[0].process.execute_session_command.side_effect = original
    nodes[0].process.delete_session.side_effect = cleanup_error

    with pytest.raises(DaytonaError, match="command start failed") as error:
        await invoke(kind, collection, timeout=1, on_stdout=lambda _chunk: None)

    assert error.value is original
    assert error.value.__cause__ is cleanup_error


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_one_rank_timeout_does_not_discard_another_ranks_completed_work(kind):
    collection, nodes = make_nodes(kind, count=2)
    nodes[0].process.get_session_command_logs_async.side_effect = never_finishes

    with pytest.raises(DaytonaTimeoutError, match="rank 0"):
        await invoke(kind, collection, timeout=1, on_stdout=lambda _chunk: None)

    nodes[1].process.get_session_command.assert_called_once()
    for node in nodes:
        own_session = node.process.create_session.call_args.args[0]
        node.process.delete_session.assert_called_once_with(own_session, request_timeout=10)


@pytest.mark.asyncio
async def test_sync_session_is_deleted_before_waiting_for_a_blocked_log_reader_thread():
    collection, nodes = make_nodes("sync")
    released = threading.Event()

    async def read_until_session_deleted(*_args):
        await asyncio.to_thread(released.wait)

    nodes[0].process.get_session_command_logs_async.side_effect = read_until_session_deleted
    nodes[0].process.delete_session.side_effect = lambda _session, **_kwargs: released.set()
    try:
        with pytest.raises(DaytonaTimeoutError):
            await asyncio.wait_for(invoke("sync", collection, timeout=1, on_stdout=lambda _chunk: None), timeout=3)
        assert released.is_set()
    finally:
        released.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["execute_session_command", "get_session_command", "get_session_command_logs"])
async def test_sync_deadline_can_interrupt_blocking_session_http_reads(method):
    collection, nodes = make_nodes("sync")
    released = threading.Event()
    mock = getattr(nodes[0].process, method)
    response = mock.return_value

    def blocked(*_args, **_kwargs):
        released.wait(1.5)
        return response

    mock.side_effect = blocked
    nodes[0].process.delete_session.side_effect = lambda *_args, **_kwargs: released.set()
    try:
        with pytest.raises(DaytonaTimeoutError, match="rank 0"):
            await invoke("sync", collection, timeout=1, on_stdout=lambda _chunk: None)
        nodes[0].process.delete_session.assert_called_once()
    finally:
        released.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_rdma_probe_has_a_bounded_execution_timeout(kind):
    collection, nodes = make_nodes(kind, count=2)
    await invoke(kind, collection, timeout=1)
    for node in nodes:
        assert node.process.exec.call_args_list[0].kwargs == {"timeout": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_stream_setup_is_bounded_with_a_positive_execution_limit(kind):
    collection, nodes = make_nodes(kind)
    await invoke(kind, collection, timeout=1, on_stdout=lambda _chunk: None)
    assert nodes[0].process.create_session.call_args.kwargs == {"request_timeout": 10}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_advisory_probe_failure_does_not_prevent_command(kind):
    collection, nodes = make_nodes(kind, count=2)
    nodes[0].process.exec.side_effect = [
        RuntimeError("probe unavailable"),
        ExecuteResponse.model_construct(exit_code=0, result="done"),
    ]
    results = await invoke(kind, collection)
    assert results[0].exit_code == 0
    nodes[0].process.exec.assert_any_call("workload")
    assert nodes[0].warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_successful_stream_survives_cleanup_failure(kind):
    collection, nodes = make_nodes(kind)
    nodes[0].process.delete_session.side_effect = RuntimeError("cleanup unavailable")
    results = await invoke(kind, collection, on_stdout=lambda _chunk: None)
    assert results[0].result == "done"
    assert "session" in " ".join(nodes[0].warnings).lower()
