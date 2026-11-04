# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unchanged parameters retain proof; repeated writes retain uncertainty."""

from __future__ import annotations

import ast
from pathlib import Path
from textwrap import indent

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize("depth", [2, 8])
def test_migrates_parameter_when_nested_loops_never_reassign_it(depth: int) -> None:
    # Given nested loop/try backedges which never write the client parameter.
    body = "x = d\nreturn x.create()\n"
    for level in range(depth):
        body = (
            f"for i{level} in values:\n    try:\n"
            + indent(body, "        ")
            + "    except ValueError:\n        continue\n"
        )
    source = "from daytona import Daytona\ndef run(d: Daytona, values):\n" + indent(
        body, "    "
    )
    # When upgrading a call whose client evidence is invariant across iterations.
    result = migrate(source)
    # Then nested control flow neither demotes the parameter nor suppresses migration.
    assert not result.items
    assert result.source == source.replace("x.create()", "x.v0.create()")


@pytest.mark.parametrize("assignment", ["x = y\n        y = d", "x, y = y, d"])
@pytest.mark.parametrize("loop", ["for i in values:", "while flag:"])
def test_reviews_receiver_when_client_crosses_multiple_backedges(
    assignment: str, loop: str
) -> None:
    # Given a client arriving only after two successive alias assignments.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values, flag):\n    x = Client()\n    y = Client()\n"
        f"    {loop}\n        if flag:\n            return x.create()\n"
        f"        {assignment}\n"
    )
    # When upgrading the call which can observe either nonclient or client iterations.
    result = migrate(source)
    # Then a multi-hop alias cannot silently lose its possible client origin.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "initial,replacement,receiver",
    [("d", "Client()", "x"), ("Client()", "d", "x"), ("d", "Client()", "d")],
)
def test_reviews_parameter_when_loop_reassigns_it(
    initial: str, replacement: str, receiver: str
) -> None:
    # Given a receiver written inside a loop instead of an invariant parameter.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def run(d: Daytona, values):\n    {receiver} = {initial}\n"
        f"    for item in values:\n        {receiver}.create()\n        {receiver} = {replacement}\n"
    )
    # When analyzing calls across iterations.
    result = migrate(source)
    # Then reassigned names still require review rather than an unsafe rewrite.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_reviews_parameter_when_loop_closure_reassigns_it() -> None:
    # Given a deferred nonlocal writer called before the next iteration.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values):\n    for item in values:\n"
        "        d.create()\n        def replace():\n"
        "            nonlocal d\n            d = Client()\n        replace()\n"
    )
    # When analyzing the receiver before the closure definition.
    result = migrate(source)
    # Then only the first iteration's annotation is insufficient proof.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_cli_migrates_when_nested_parameter_is_invariant(tmp_path: Path) -> None:
    # Given an actual project with an invariant client inside nested loop/try code.
    source = (
        "from daytona import Daytona\ndef run(d: Daytona, values):\n"
        "    for first in values:\n        try:\n            for second in values:\n"
        "                try:\n                    x = d\n                    return x.create()\n"
        "                except ValueError:\n                    continue\n"
        "        except ValueError:\n            continue\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When running the real strict upgrade entry point.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then it succeeds with the executable call migrated, not a false FIXME.
    assert status == 0
    assert app.read_text() == source.replace("x.create()", "x.v0.create()")


def test_cli_requires_review_when_loop_alias_crosses_two_names(tmp_path: Path) -> None:
    # Given a project whose receiver becomes a client only on later iterations.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values, flag):\n    x = Client()\n    y = Client()\n"
        "    for item in values:\n        if flag:\n            return x.create()\n"
        "        x = y\n        y = d\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When running the real strict upgrade entry point.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then review blocks strict success without falsely rewriting executable code.
    assert status == 1
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
