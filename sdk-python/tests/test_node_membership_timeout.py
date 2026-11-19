# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona._async.sandbox import AsyncSandboxNodeCollection
from daytona._sync.sandbox import SandboxNodeCollection
from daytona.common.errors import DaytonaError


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("case", ["bounded", "late", "unlimited"])
async def test_membership_poll_and_refresh_deadlines(monkeypatch, kind, case):
    clock = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds

    async def async_sleep(seconds):
        sleep(seconds)

    monkeypatch.setattr("daytona._sync.sandbox.time.sleep", sleep)
    monkeypatch.setattr("daytona._async.sandbox.asyncio.sleep", async_sleep)
    mock = MagicMock if kind == "sync" else AsyncMock
    completed = SimpleNamespace(id="operation-1", type="ADD", status="COMPLETED")

    def poll(*_args, **_kwargs):
        if case == "late":
            clock[0] = 11.0
        return completed

    operation = SimpleNamespace(id="operation-1", type="REMOVE", status="RUNNING")
    api = SimpleNamespace(
        get_gang_membership_operation=mock(side_effect=poll), remove_sandbox_nodes=mock(return_value=operation)
    )
    sandbox = SimpleNamespace(id="head-1", _sandbox_api=api, refresh_data=mock(), topology_generation=4)
    nodes = SandboxNodeCollection(sandbox) if kind == "sync" else AsyncSandboxNodeCollection(sandbox)
    timeout = 0 if case == "unlimited" else 10
    result = nodes.release(timeout=timeout)
    if kind == "async":
        result = await result
    assert result is completed
    remaining = None if case == "unlimited" else 9.0
    api.get_gang_membership_operation.assert_called_once_with("head-1", "operation-1", _request_timeout=remaining)
    if case == "late":
        sandbox.refresh_data.assert_called_once_with(request_timeout=10.0)
    else:
        assert sandbox.refresh_data.call_args.kwargs == {"request_timeout": remaining}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("status", ["RUNNING", "COMPLETED"])
async def test_release_preserves_accepted_operation_when_a_read_fails(kind, status, monkeypatch):
    mock = MagicMock if kind == "sync" else AsyncMock
    operation = SimpleNamespace(id="op-release", status=status)
    api = SimpleNamespace(
        remove_sandbox_nodes=mock(return_value=operation),
        get_gang_membership_operation=mock(side_effect=DaytonaError("unavailable")),
    )
    sandbox = SimpleNamespace(
        id="head",
        _sandbox_api=api,
        topology_generation=4,
        refresh_data=mock(side_effect=[None, DaytonaError("unavailable")]),
    )
    nodes = SandboxNodeCollection(sandbox) if kind == "sync" else AsyncSandboxNodeCollection(sandbox)
    monkeypatch.setattr("daytona._sync.sandbox.time.sleep", MagicMock())
    monkeypatch.setattr("daytona._async.sandbox.asyncio.sleep", AsyncMock())

    async def release():
        return await nodes.release(timeout=0) if kind == "async" else nodes.release(timeout=0)

    if status == "RUNNING":
        with pytest.raises(DaytonaError) as caught:
            await release()
        assert caught.value.operation_id == operation.id
    else:
        assert await release() is operation
    api.remove_sandbox_nodes.assert_called_once()
