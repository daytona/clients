# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona._async.sandbox import AsyncSandboxNodeCollection
from daytona._sync.sandbox import SandboxNodeCollection
from daytona.common.errors import DaytonaTimeoutError


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

    api = SimpleNamespace(get_gang_membership_operation=mock(side_effect=poll))
    sandbox = SimpleNamespace(id="head-1", _sandbox_api=api, refresh_data=mock())
    nodes = SandboxNodeCollection(None) if kind == "sync" else AsyncSandboxNodeCollection(None)
    nodes._sandbox = sandbox
    operation = SimpleNamespace(id="operation-1", type="ADD", status="RUNNING")
    deadline = None if case == "unlimited" else 10.0

    async def wait():
        if kind == "async":
            return await nodes._wait(operation, deadline)
        return nodes._wait(operation, deadline)

    if case == "late":
        with pytest.raises(DaytonaTimeoutError):
            await wait()
        sandbox.refresh_data.assert_not_called()
    else:
        assert await wait() is completed
        remaining = None if case == "unlimited" else 9.0
        api.get_gang_membership_operation.assert_called_once_with("head-1", "operation-1", _request_timeout=remaining)
        sandbox.refresh_data.assert_called_once_with(request_timeout=remaining)
