# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Target expressions preserve source validity and receiver migration."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize("escape", [r"\x00", r"\x01", r"\t", r"\n", r"\x7f", r"\u200b"])
@pytest.mark.parametrize("call", ["patch", "import_module"])
def test_target_preserves_value_when_suffix_is_nonprintable(
    escape: str, call: str
) -> None:
    # Given a valid escaped target rather than a literal control character.
    source = f'from unittest.mock import patch\nfrom importlib import import_module\n{call}("daytona_sdk.sandbox.{escape}")\n'
    expected = ast.parse(source.replace("daytona_sdk.sandbox", "daytona.v0.sandbox"))
    # When the target namespace migrates.
    result = migrate(source)
    # Then generated Python parses to the exact intended string value.
    assert ast.dump(ast.parse(result.source)) == ast.dump(expected)


@pytest.mark.parametrize("keyword", ["with", "async with"])
@pytest.mark.parametrize(
    "target",
    [
        "slots[client.create()]",
        "slots[client.create()].value",
        "(saved, slots[client.create()])",
    ],
)
def test_migrates_when_with_target_evaluates_client_call(
    keyword: str, target: str
) -> None:
    # Given an executable client call inside a context-manager assignment target.
    source = f"from daytona import Daytona\nasync def run():\n client = Daytona()\n {keyword} x as {target}:\n  pass\n"
    # When the flow visitor evaluates the with target before binding it.
    result = migrate(source)
    # Then the target's call receives the same migration as any other expression.
    assert result.source == source.replace("client.create()", "client.v0.create()")
