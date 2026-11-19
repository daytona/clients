# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Execution deadlines for the synchronous and asynchronous all-node helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona._async.sandbox import AsyncSandboxNode, AsyncSandboxNodeCollection
from daytona._sync.sandbox import SandboxNode, SandboxNodeCollection
from daytona.common.errors import DaytonaError, DaytonaTimeoutError, DaytonaValidationError
from daytona.common.process import ExecuteResponse, ExecutionArtifacts

from .conftest import make_sandbox_dto


def make_nodes(kind, count=1):
    collection = SandboxNodeCollection(None) if kind == "sync" else AsyncSandboxNodeCollection(None)
    mock = MagicMock if kind == "sync" else AsyncMock
    response = ExecuteResponse.model_construct(
        exit_code=0, result="done", artifacts=ExecutionArtifacts(stdout="done", charts=[])
    )
    nodes = []
    for rank in range(count):
        dto = make_sandbox_dto(node_count=count).nodes[rank]
        node_type = SandboxNode if kind == "sync" else AsyncSandboxNode
        node = node_type(dto, MagicMock(), "https://toolbox.example.com", MagicMock(), "python", MagicMock())
        node.process.exec = mock(return_value=response)
        nodes.append(node)
    collection._replace(nodes)
    return collection, nodes


async def invoke(kind, collection, **kwargs):
    if kind == "sync":
        return await asyncio.to_thread(collection.process.exec, "workload", **kwargs)
    return await collection.process.exec("workload", **kwargs)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_rank_failure_preserves_successful_results(kind):
    collection, nodes = make_nodes(kind, count=2)
    error = DaytonaTimeoutError("rank 0 exceeded its timeout")
    nodes[0].process.exec.side_effect = [nodes[0].process.exec.return_value, error]

    with pytest.raises(DaytonaTimeoutError) as caught:
        await invoke(kind, collection, timeout=1)

    assert caught.value.node_failures == {0: error}
    assert caught.value.node_results[1].result == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_rdma_probe_has_a_bounded_execution_timeout(kind):
    collection, nodes = make_nodes(kind, count=2)
    await invoke(kind, collection, timeout=1)
    await invoke(kind, collection, timeout=1)
    replacement = nodes[1]._to_dto()
    replacement.incarnation += 1
    nodes[1].update(replacement)
    await invoke(kind, collection, timeout=1)
    for node, expected in zip(nodes, (1, 2), strict=True):
        probes = [call for call in node.process.exec.call_args_list if call.kwargs == {"timeout": 5}]
        assert len(probes) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_advisory_probe_failure_does_not_prevent_command(kind):
    collection, nodes = make_nodes(kind, count=2)
    nodes[0].process.exec.side_effect = [
        RuntimeError("probe unavailable"),
        nodes[0].process.exec.return_value,
    ]
    results = await invoke(kind, collection)
    assert results[0].result == results[0].artifacts.stdout == "done"
    assert results[0].exit_code == 0
    nodes[0].process.exec.assert_any_call("workload")
    assert nodes[0].warnings
