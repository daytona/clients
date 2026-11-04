# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only paths reaching a branch join contribute receiver bindings."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "exit_statement", ["return", "raise RuntimeError()", "continue", "break"]
)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("receiver", ["Client", "Daytona"])
def test_uses_reaching_binding_when_other_branch_exits(
    exit_statement: str, reverse: bool, receiver: str
) -> None:
    # Given conflicting evidence on a path that exits before the call.
    exited = f"x = {'Daytona' if receiver == 'Client' else 'Client'}()\n            {exit_statement}"
    reaching = f"x = {receiver}()"
    left, right = (reaching, exited) if reverse else (exited, reaching)
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(flag):\n    for _ in range(2):\n"
        f"        if flag:\n            {left}\n        else:\n            {right}\n"
        "        x.get('url')\n"
    )
    # When analyzing the call after the branch, but still inside the loop.
    result = migrate(source)
    # Then only the reaching path owns the receiver: HTTPX stays unchanged.
    assert result.items == ()
    assert result.source == (
        source.replace("x.get(", "x.v0.get(") if receiver == "Daytona" else source
    )


def test_preserves_httpx_when_return_branch_has_no_assignment() -> None:
    # Given an existing client proof and a guard that returns before its use.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(flag):\n    x = Daytona()\n    if flag:\n        return\n"
        "    else:\n        x = Client()\n    x.get('url')\n"
    )
    # When joining the terminated path with the HTTPX-only path.
    result = migrate(source)
    # Then a non-reaching proof cannot manufacture a FIXME.
    assert result.items == ()
    assert result.source == source


def test_uses_reaching_binding_when_nested_branches_both_exit() -> None:
    # Given an entire branch whose nested alternatives both return or raise.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(flag, other):\n    x = Daytona()\n"
        "    if flag:\n        if other:\n            return\n"
        "        else:\n            raise RuntimeError()\n"
        "    else:\n        x = Client()\n    x.get('url')\n"
    )
    # When joining with the single remaining HTTPX path.
    result = migrate(source)
    # Then recursively terminated paths cannot contaminate the join.
    assert result.items == ()
    assert result.source == source


def test_keeps_uncertainty_when_nested_branch_can_reach_join() -> None:
    # Given a branch with one exiting subpath and one client-bearing subpath.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(flag, other):\n    if flag:\n        if other:\n            return\n"
        "        x = Daytona()\n    else:\n        x = Client()\n    x.get('url')\n"
    )
    # When joining paths that really can reach the call.
    result = migrate(source)
    # Then a nested return is not enough to discard the whole parent branch.
    assert [item["kind"] for item in result.items] == ["fixme"]
