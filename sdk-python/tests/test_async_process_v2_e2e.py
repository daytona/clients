# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from daytona import AsyncDaytona

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        os.getenv("DAYTONA_V2_E2E") != "1",
        reason="Set DAYTONA_V2_E2E=1 to run live Process v2 e2e coverage.",
    ),
]


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def async_daytona_client():
    async with AsyncDaytona() as daytona:
        yield daytona


async def test_async_process_v2_rehydrate_and_resume(async_daytona_client: AsyncDaytona) -> None:
    sandbox = await async_daytona_client.create(timeout=120)
    try:
        handle = await sandbox.process.start(shell_command='printf "one\\n"; sleep 0.2; printf "two\\n"')
        serialized = handle.to_json()
        result = await handle.wait()

        fresh = await async_daytona_client.get(serialized["sandboxId"])
        rehydrated = fresh.process.from_json(serialized)
        page = await rehydrated.logs()

        assert result.reason == "exited"
        assert [frame.data for frame in page.frames] == ["one\n", "two\n"]
        if page.next_cursor:
            resumed = await rehydrated.logs(cursor=page.frames[0].cursor)
            assert [frame.data for frame in resumed.frames] == ["two\n"]
    finally:
        await async_daytona_client.delete(sandbox)
