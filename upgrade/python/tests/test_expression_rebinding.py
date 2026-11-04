# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Expression rebinding and list reinitialization cannot prove SDK receivers."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "expression",
    [
        "factory(factory := Daytona).create()",
        "result = factory(factory := Daytona)\nresult.create()",
        "[client, (client := Daytona())][0].create()",
        "clients = [client, (client := Daytona())]\nclients[0].create()",
        "(client, (client := Daytona()))[0].create()",
    ],
)
def test_receiver_stays_unproven_when_later_expression_rebinds_name(
    expression: str,
) -> None:
    # Given an unrelated receiver evaluated before a later walrus SDK rebinding.
    source = (
        "from daytona import Daytona\nfactory = Other\nclient = Other()\n"
        + expression
        + "\n"
    )
    # When inferring the completed expression.
    result = migrate(source)
    # Then its earlier value never inherits the later SDK binding.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "mutation", ["clients.__init__([Other()])", "list.__init__(clients, [Other()])"]
)
def test_collection_stays_unproven_when_list_is_reinitialized(mutation: str) -> None:
    # Given a list whose original SDK elements are replaced through __init__.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\n"
        + mutation
        + "\nclients[0].create()\n"
    )
    # When resolving a call after reinitialization.
    result = migrate(source)
    # Then no stale element proof redirects the replacement object.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
