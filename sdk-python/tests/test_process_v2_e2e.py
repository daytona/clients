# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

import pytest

from daytona import Daytona

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("DAYTONA_V2_E2E") != "1",
        reason="Set DAYTONA_V2_E2E=1 to run live Process v2 e2e coverage.",
    ),
]


@pytest.fixture(scope="module")
def daytona_client() -> Daytona:
    return Daytona()


def test_process_v2_rehydrate_and_resume(daytona_client: Daytona) -> None:
    sandbox = daytona_client.create(timeout=120)
    try:
        handle = sandbox.process.start(shell_command='printf "one\\n"; sleep 0.2; printf "two\\n"')
        serialized = handle.to_json()
        result = handle.wait()

        fresh = daytona_client.get(serialized["sandboxId"])
        rehydrated = fresh.process.from_json(serialized)
        page = rehydrated.logs()

        assert result.reason == "exited"
        assert [frame.data for frame in page.frames] == ["one\n", "two\n"]
        if page.next_cursor:
            resumed = rehydrated.logs(cursor=page.frames[0].cursor)
            assert [frame.data for frame in resumed.frames] == ["two\n"]
    finally:
        daytona_client.delete(sandbox)
