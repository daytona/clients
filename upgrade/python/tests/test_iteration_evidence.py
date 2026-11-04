# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Iteration and exception-group writes cannot disappear from receiver evidence."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from textwrap import indent

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.skipif(sys.version_info < (3, 11), reason="except* requires Python 3.11")
@pytest.mark.parametrize("ending", ["", "        raise RuntimeError()\n"])
@pytest.mark.parametrize("initial,replacement", [("d", "Client()"), ("Client()", "d")])
def test_reviews_receiver_when_preceding_group_handler_rebinds(
    initial: str, replacement: str, ending: str
) -> None:
    # Given two handlers which may both execute for one exception group.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def run(d: Daytona):\n    x = {initial}\n"
        "    try:\n        risky()\n"
        f"    except* ValueError:\n        x = {replacement}\n"
        f"{ending}"
        "    except* TypeError:\n        x.get('url')\n"
    )
    # When upgrading the call in the second handler.
    result = migrate(source)
    # Then both the skipped and executed first-handler paths require review.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "body",
    [
        "for item in values:\n    x.get('url')\n    x = d",
        "[(x := d) for item in values]\nx.get('url')",
    ],
)
def test_cli_requires_review_when_iteration_can_change_receiver(
    tmp_path: Path, body: str
) -> None:
    # Given a real project with receiver uncertainty across iteration boundaries.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values):\n    x = Client()\n" + indent(body, "    ") + "\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When invoking the real strict upgrade entry point.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the CLI fails visibly and preserves the executable receiver call.
    assert status == 1
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "body",
    [
        "for item in values:\n    x.get('url')\n    x = d",
        "while flag:\n    x.get('url')\n    x = d",
        "for item in values:\n    x.get('url')\n    x = d\n    continue",
        "while flag:\n    x.get('url')\n    x = d\n    continue",
        "for item in values:\n    if flag:\n        x = d\n        continue\n    x.get('url')",
        "while x.get('url'):\n    x = d",
    ],
)
def test_reviews_receiver_when_client_flows_through_backedge(body: str) -> None:
    # Given a receiver initially unrelated to Daytona but replaced for later iterations.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values, flag):\n    x = Client()\n"
        + indent(body, "    ")
        + "\n"
    )
    # When upgrading the call which can observe a previous iteration's assignment.
    result = migrate(source)
    # Then uncertainty is visible, not an unreported runtime-guard failure.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "body",
    [
        "for item in values:\n    x.get('url')\n    x = d\n    break",
        "while flag:\n    x.get('url')\n    x = d\n    break",
        "for item in values:\n    x.get('url')\n    x = d\n    return",
        "[(lambda: (x := d)) for item in values]\nx.get('url')",
    ],
)
def test_keeps_nonclient_when_client_write_cannot_reach_call(body: str) -> None:
    # Given a client assignment which cannot change this receiver evaluation.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values, flag):\n    x = Client()\n"
        + indent(body, "    ")
        + "\n"
    )
    # When analyzing abrupt loop exits or a nested lambda's separate local.
    result = migrate(source)
    # Then speculative analysis produces neither rewrites nor false review items.
    assert not result.items
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "expression",
    [
        "[(x := d) for item in values]",
        "{(x := d) for item in values}",
        "{item: (x := d) for item in values}",
        "[(item, x) for item in values if (x := d)]",
        "[[(x := d) for other in values] for item in values]",
        "list((x := d) for item in values)",
        "pending = ((x := d) for item in values)\nlist(pending)",
    ],
)
def test_reviews_receiver_when_comprehension_walrus_binds_enclosing_scope(
    expression: str,
) -> None:
    # Given an enclosing binding possibly replaced by a comprehension's walrus.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def run(d: Daytona, values):\n    x = Client()\n"
        + indent(expression, "    ")
        + "\n    x.get('url')\n"
    )
    # When upgrading the later enclosing-scope call.
    result = migrate(source)
    # Then a possibly empty or deferred comprehension provides review, not proof.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
