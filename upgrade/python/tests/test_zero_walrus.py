# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Possibly empty comprehensions cannot erase client evidence or prove binding."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "initial,replacement", [("x = d\n    ", "Client()"), ("", "d")]
)
@pytest.mark.parametrize(
    "expression",
    [
        "[(x := VALUE) for i in values]",
        "{(x := VALUE) for i in values}",
        "{i: (x := VALUE) for i in values}",
        "pending = ((x := VALUE) for i in values)",
    ],
)
def test_reviews_receiver_when_walrus_may_never_execute(
    initial: str, replacement: str, expression: str
) -> None:
    # Given either a client that survives an empty comprehension or an unbound alias.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def run(d: Daytona, values):\n    {initial}{expression.replace('VALUE', replacement)}\n"
        "    return x.create()\n"
    )
    # When joining zero executions with the walrus assignment.
    result = migrate(source)
    # Then possible client use requires review, never proof or a silent miss.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("iterable", ["[]", "()"])
@pytest.mark.parametrize(
    "expression",
    [
        "[(x := Client()) for i in EMPTY]",
        "{(x := Client()) for i in EMPTY}",
        "{i: (x := Client()) for i in EMPTY}",
        "pending = ((x := Client()) for i in EMPTY)",
    ],
)
def test_routes_receiver_when_empty_literal_prevents_walrus_execution(
    iterable: str, expression: str
) -> None:
    # Given a proven client and a walrus whose iteration body never executes.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona):\n    x = d\n"
        f"    {expression.replace('EMPTY', iterable)}\n    return x.create()\n"
    )
    # When migrating the call after the definitely empty comprehension.
    result = migrate(source)
    # Then the original client proof survives without a manual-review item.
    assert result.source == source.replace("x.create()", "x.v0.create()")
    assert result.items == ()
