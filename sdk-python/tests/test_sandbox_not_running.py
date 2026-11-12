# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A toolbox call on a stopped sandbox: the proxy answers 409
``SANDBOX_NOT_RUNNING`` (source ``DAYTONA_PROXY``), which every toolbox path
raises as ``SandboxNotRunningError``, a ``ConflictError`` and not a
``NotFoundError``.

Mirrors `stopped sandbox toolbox calls` in `sdk-typescript/tests/unit.test.ts`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from _handles import lazy_sandbox

from daytona.v2 import ConflictError, NotFoundError, SandboxNotRunningError
from daytona.v2.daytona import AsyncDaytona
from daytona.v2.sandbox import AsyncSandbox
from daytona.v2.types import DaytonaConfig

TOOLBOX_URL = "http://toolbox.invalid/sandboxes/sbx"

NOT_RUNNING = {
    "code": "SANDBOX_NOT_RUNNING",
    "message": "sandbox is not running",
    "source": "DAYTONA_PROXY",
    "trace_id": "trace-1",
}


def _stopped_sandbox() -> AsyncSandbox:
    client = AsyncDaytona(DaytonaConfig(api_key="key"))
    sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX_URL, access_token="caller_tok")
    sandbox._update(sandbox_class="linux")
    assert sandbox._toolbox is not None  # type: ignore[attr-defined]
    sandbox._toolbox._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(lambda request: httpx.Response(409, json=NOT_RUNNING))
    )
    return sandbox


async def _ignore(_event: object) -> None:
    return None


CALLS: dict[str, Callable[[AsyncSandbox, Path], Awaitable[object]]] = {
    "exec": lambda sandbox, _dir: sandbox.exec("echo hi"),
    "fs.list": lambda sandbox, _dir: sandbox.fs.list("/home"),
    "fs.read_bytes": lambda sandbox, _dir: sandbox.fs.read_bytes("/home/a.txt"),
    "fs.download_file": lambda sandbox, dir: sandbox.fs.download_file("/home/a.txt", dir / "a.txt"),
    "fs.watch_dir": lambda sandbox, _dir: sandbox.fs.watch_dir("/home", _ignore),
    "git.get_config": lambda sandbox, _dir: sandbox.git.get_config("user.name"),
}


@pytest.mark.anyio
@pytest.mark.parametrize("name", CALLS)
async def test_a_stopped_sandbox_raises_sandbox_not_running(name: str, tmp_path: Path) -> None:
    sandbox = _stopped_sandbox()

    with pytest.raises(SandboxNotRunningError) as info:
        await CALLS[name](sandbox, tmp_path)

    error = info.value
    assert isinstance(error, ConflictError), name
    assert not isinstance(error, NotFoundError), name
    assert (error.status_code, error.code, error.source, error.trace_id) == (
        409,
        "SANDBOX_NOT_RUNNING",
        "DAYTONA_PROXY",
        "trace-1",
    )
