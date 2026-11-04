# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for context, definition-time yield and store-order proofs."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_flow import migrate


@pytest.mark.parametrize("manager", ["d", "ctx.nullcontext(d)"])
def test_routes_result_when_async_stack_receives_explicit_cm_keyword(
    manager: str,
) -> None:
    # Given a proven stack with its single context supplied by keyword.
    source = (
        "import contextlib as ctx\nfrom daytona import Daytona\n"
        "async def run(d: Daytona):\n    stack = ctx.AsyncExitStack()\n"
        f"    value = await stack.enter_async_context(cm={manager})\n"
        "    value.create()\n"
    )
    # When migrating the returned context entry value.
    result = migrate(source)
    # Then keyword spelling preserves the same client proof as positional spelling.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "arguments",
    ["other=d", "**options", "cm=d, **options", "cm=d, other=d", "d, cm=d"],
)
def test_preserves_result_when_async_stack_arguments_are_not_single_context(
    arguments: str,
) -> None:
    # Given an unsupported or ambiguous context argument signature.
    source = (
        "import contextlib as ctx\nfrom daytona import Daytona\n"
        "async def run(d: Daytona, options):\n    stack = ctx.AsyncExitStack()\n"
        f"    value = await stack.enter_async_context({arguments})\n"
        "    value.create()\n"
    )
    # When inspecting the entry result without guessing argument binding.
    result = migrate(source)
    # Then an invalid or unpacked call cannot establish a client proof.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "definition",
    [
        "def inner(value=(yield d)): pass",
        "async def inner(value=(yield d)): pass",
        "def inner(*, value=(yield d)): pass",
        "@(yield d)\n    def inner(): pass",
        "def inner(value: (yield d)): pass",
        "def inner() -> (yield d): pass",
        "factory = lambda value=(yield d): None",
        "@(yield d)\n    class Inner: pass",
        "class Inner((yield d)): pass",
        "class Inner(metaclass=(yield d)): pass",
    ],
)
@pytest.mark.parametrize("ending", ["", "    yield 'foreign'\n"])
def test_reviews_element_when_nested_definition_evaluates_outer_yield(
    definition: str, ending: str
) -> None:
    # Given a client yield evaluated while defining a nested scope, not in its body.
    source = (
        "from daytona import Daytona\n"
        f"def values(d: Daytona):\n    {definition}\n{ending}"
        "for value in values(d):\n    value.create()\n"
    )
    try:
        compile(source, "app.py", "exec", dont_inherit=True)
    except SyntaxError as error:
        pytest.skip(f"Source is invalid on Python {sys.version_info[:2]}: {error.msg}")
    # When inspecting the outer generator's possible elements.
    result = migrate(source)
    # Then bounded inference retains client evidence without claiming exact flow.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("seed", ["0", "1", "2", "3"])
@pytest.mark.parametrize(
    "expression",
    [
        "owner.client.create() or [None for alias in [owner] for alias.client in [unknown()]]",
        "owner.client.create() if flag else [None for alias in [owner] for alias.client in [unknown()]]",
    ],
)
def test_cli_reviews_member_when_later_operand_rebinds_owner_alias(
    tmp_path: Path, seed: str, expression: str
) -> None:
    # Given distinct old alias/owner types, plus a later store through the new alias.
    source = (
        "from daytona import Daytona\nclass Owner: pass\nclass Other: pass\n"
        "owner = Owner()\nowner.client = Daytona()\nalias = Other()\n"
        f"{expression}\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When running the strict CLI under independent frozenset iteration orders.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "daytona_upgrade",
            str(tmp_path),
            "--force",
            "--yes",
            "--strict",
            "--json",
        ],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the uncertain member requires review and is never redirected to v0.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FIXME(daytona-upgrade)" in app.read_text()
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
