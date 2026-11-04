# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The real built SDK demonstrates why async class factories require review."""

from __future__ import annotations

import os
import subprocess

import pytest
from test_synchronous_mock_specs import migrate


@pytest.mark.parametrize(
    "definition,spec",
    [
        (
            "class Factory:\n    @staticmethod\n    def select() -> type[AsyncDaytona]:\n        return AsyncDaytona\n",
            "Factory.select()",
        ),
        (
            "class Factory:\n    def select(self) -> type[AsyncDaytona]:\n        return AsyncDaytona\n",
            "Factory().select()",
        ),
        ("def select() -> type[AsyncDaytona]:\n    return AsyncDaytona\n", "select()"),
    ],
)
def test_preserves_manual_review_when_real_root_mock_would_return_sync_value(
    definition: str, spec: str
) -> None:
    # Given the bundled SDK and future annotations preceding its import.
    interpreter = os.environ.get("DAYTONA_UPGRADE_TEST_SDK_PYTHON")
    if interpreter is None:
        pytest.skip("set DAYTONA_UPGRADE_TEST_SDK_PYTHON to a bundled SDK interpreter")
    source = (
        "from __future__ import annotations\n"
        + definition
        + "from daytona import AsyncDaytona\nfrom unittest.mock import MagicMock\n"
        + f"client = MagicMock(spec={spec})\n"
        + "async def run():\n    return await client.create()\n"
    )
    result = migrate(source)
    runtime = (
        source
        + "from inspect import isawaitable\n"
        + "assert not isawaitable(client.v0.create())\n"
    )

    # When the actual root's property-backed v0 mock is inspected without network IO.
    completed = subprocess.run(
        [interpreter, "-I", "-c", runtime], capture_output=True, text=True, check=False
    )

    # Then that value cannot be awaited, and migration leaves the call for review.
    assert completed.returncode == 0, completed.stderr
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
