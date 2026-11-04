# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reaching compound-statement paths retain client evidence, never stale proof."""

from __future__ import annotations

import ast
import sys
from textwrap import indent

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body,outcome",
    [
        ("try:\n    x = d\nexcept ValueError:\n    return", "rewrite"),
        (
            "try:\n    x = d\nexcept ValueError:\n    x = Client()\n    return",
            "rewrite",
        ),
        ("try:\n    x = d\nexcept ValueError:\n    x = Client()", "fixme"),
        ("while True:\n    x = d\n    break", "rewrite"),
        ("x = Client()\nwhile True:\n    x = d\n    break", "rewrite"),
        (
            "while True:\n    if flag:\n        x = d\n        break\n    return",
            "rewrite",
        ),
        ("for item in [1]:\n    x = d\n    break", "fixme"),
        ("for item in []:\n    pass\nelse:\n    x = d", "rewrite"),
        ("for item in []:\n    return\nelse:\n    x = d", "rewrite"),
        ("for item in values:\n    x = Client()\n    break\nelse:\n    x = d", "fixme"),
        ("with unknown():\n    x = d", "fixme"),
        ("with open(__file__):\n    x = d", "rewrite"),
        (
            "x = Client()\nfor item in values:\n    if flag:\n        x = d\n        continue\n    x = Client()",
            "fixme",
        ),
        (
            "x = Client()\nwhile flag:\n    x = d\n    continue\n    x = Client()",
            "fixme",
        ),
        ("try:\n    x = Client()\nexcept ValueError:\n    x = d\n    return", "clean"),
        ("while True:\n    x = Client()\n    break\n    x = d", "clean"),
        ("for item in values:\n    pass\nelse:\n    x = Client()", "clean"),
        ("with unknown():\n    x = Client()", "clean"),
        (
            "x = Client()\nfor item in values:\n    x = Client()\n    continue\n    x = d",
            "clean",
        ),
        (
            "while True:\n    for item in values:\n        x = Client()\n        break\n    x = d\n    break",
            "rewrite",
        ),
        (
            "for item in values:\n    for other in values:\n        break\nelse:\n    x = d",
            "rewrite",
        ),
        (
            "try:\n    x = Client()\nexcept ValueError:\n    return\nfinally:\n    x = d",
            "rewrite",
        ),
        (
            "try:\n    x = d\nexcept ValueError:\n    return\nfinally:\n    pass",
            "fixme",
        ),
        (
            "while True:\n    try:\n        x = d\n        break\n    finally:\n        x = Client()",
            "fixme",
        ),
        (
            "x = d\nwhile flag:\n    try:\n        break\n    finally:\n        x = Client()",
            "fixme",
        ),
        (
            "while True:\n    try:\n        x = d\n        break\n    finally:\n        return\nx = Client()",
            "clean",
        ),
    ],
)
def test_preserves_evidence_when_compound_paths_join(body: str, outcome: str) -> None:
    # Given a client alias whose provenance differs along compound control flow.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(d: Daytona, flag, values):\n"
        + indent(body, "    ")
        + "\n    x.get('url')\n"
    )
    # When upgrading the actual call after the compound statement.
    result = migrate(source)
    # Then only unconditional client evidence permits rewriting.
    assert [item["kind"] for item in result.items] == (
        ["fixme"] if outcome == "fixme" else []
    )
    expected = source.replace("x.get(", "x.v0.get(") if outcome == "rewrite" else source
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match requires Python 3.10")
@pytest.mark.parametrize(
    "arms,outcome",
    [
        ("case True:\n    return\ncase _:\n    x = d", "rewrite"),
        ("case True:\n    x = d\ncase _:\n    x = Client()", "fixme"),
        ("case True:\n    x = d", "fixme"),
        ("case True:\n    x = d\n    return\ncase _:\n    x = Client()", "clean"),
        ("case _ if flag:\n    x = d", "fixme"),
    ],
)
def test_merges_reaching_arms_when_matching(arms: str, outcome: str) -> None:
    # Given matched paths, optionally including an unmatched or exiting path.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(d: Daytona, flag):\n    match flag:\n"
        + indent(arms, "        ")
        + "\n    x.get('url')\n"
    )
    # When joining the arms before the receiver call.
    result = migrate(source)
    # Then returning arms cannot erase or manufacture receiver evidence.
    assert [item["kind"] for item in result.items] == (
        ["fixme"] if outcome == "fixme" else []
    )
    expected = source.replace("x.get(", "x.v0.get(") if outcome == "rewrite" else source
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match requires Python 3.10")
def test_keeps_guard_writes_when_matching_arm_returns() -> None:
    # Given a guard that changes the receiver even if the arm does not run.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(d: Daytona, flag):\n    x = d\n    match flag:\n"
        "        case _ if (x := Client()):\n            return\n"
        "    x.get('url')\n"
    )
    # When joining the guard-false path after the match.
    result = migrate(source)
    # Then the pre-guard client proof cannot justify redirecting the call.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match requires Python 3.10")
def test_reviews_capture_when_partial_match_can_leave_a_binding() -> None:
    # Given a pattern whose partial captures need not be rolled back on failure.
    source = (
        "from daytona import Daytona\ndef launch(d: Daytona, value):\n"
        "    x = d\n    match value:\n        case [x, 1]:\n            return\n"
        "    x.get('url')\n"
    )
    # When joining the unmatched pattern path.
    result = migrate(source)
    # Then an earlier client binding cannot certify the possibly replaced value.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
