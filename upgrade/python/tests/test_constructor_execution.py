# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor protection follows eager evaluation, not syntactic containment."""

from __future__ import annotations

from textwrap import indent

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "access",
    [
        "[client.v0.list() for _ in range(1)]",
        "{client.v0 for _ in range(1)}",
        "{i: client.v0 for i in range(1)}",
        "[x for x in client.v0.list()]",
        "[x for client in client.v0.list()]",
        "(x for x in client.v0.list())",
        "[[client.v0 for _ in range(1)] for _ in range(1)]",
        "alias = client\n[alias.v0 for _ in range(1)]",
        "client.v0 and condition",
        "client.v0 or fallback",
        "left if client.v0 else right",
    ],
)
def test_omits_constructor_warning_when_enclosing_client_is_accessed_eagerly(
    access: str,
) -> None:
    # Given a comprehension or unconditional expression using the outer client.
    source = (
        "from daytona import Daytona, DaytonaError\ndef run():\n    try:\n"
        "        client = Daytona()\n"
        + indent(access, "        ")
        + "\n    except DaytonaError:\n        pass\n"
    )
    # When migrating its protected construction.
    result = migrate(source)
    # Then the eager first access already protects credential validation.
    assert not any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "access",
    [
        "[client.v0 for client in xs]",
        "[client.v0 for _, client in xs]",
        "[[client.v0 for _ in range(1)] for client in xs]",
        "{client.v0 for client in xs}",
        "{i: client.v0 for i, client in xs}",
        "(client.v0 for _ in range(1))",
        "if condition:\n    client.v0",
        "if condition:\n    pass\nelse:\n    client.v0",
        "for _ in values:\n    client.v0",
        "while condition:\n    client.v0",
        "condition and client.v0",
        "condition or client.v0",
        "client.v0 if condition else None",
        "None if condition else client.v0",
        "lambda: client.v0",
        "def later():\n    return client.v0",
        "async def later():\n    return client.v0",
        "if condition:\n    alias = client\nalias.v0",
        "return\nclient.v0",
        "raise RuntimeError\nclient.v0",
    ],
)
def test_keeps_constructor_warning_when_access_is_local_lazy_or_conditional(
    access: str,
) -> None:
    # Given a same-spelled local receiver or a first access that may never run.
    source = (
        "from daytona import Daytona, DaytonaError\ndef run():\n    try:\n"
        "        client = Daytona()\n"
        + indent(access, "        ")
        + "\n    except DaytonaError:\n        pass\n"
    )
    # When migrating its protected construction.
    result = migrate(source)
    # Then the original construction still requires manual first-use protection.
    assert any(item["kind"] == "fixme" and item["line"] == 4 for item in result.items)
