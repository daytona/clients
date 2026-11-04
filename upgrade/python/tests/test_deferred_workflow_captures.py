# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Deferred lifecycle calls belong to the captured sandbox workflow."""

from __future__ import annotations

import ast
from textwrap import indent

import pytest
from test_suggestions import suggest, suggestion_mapping
from test_workflow_binding_lifetimes import analyzed

from daytona_upgrade.suggestions import guidance


@pytest.mark.parametrize(
    "body",
    [
        "def later():\n    box.stop()\n",
        "async def later():\n    box.stop()\n",
        "def later():\n    def deeper():\n        box.stop()\n",
        "later = lambda: box.stop()\n",
        "def later():\n    d.v0.stop(box)\n",
        "later = lambda: d.v0.stop(box)\n",
        "def later(box=d.v0.stop(box)):\n    pass\n",
        "later = lambda box=d.v0.stop(box): None\n",
    ],
)
@pytest.mark.parametrize("function_scope", [False, True])
def test_keeps_workflow_on_v0_when_deferred_body_captures_sandbox(
    body: str, function_scope: bool
) -> None:
    # Given a lifecycle call deferred in a body capturing this workflow's sandbox.
    workflow = "box = d.v0.create()\n" + body
    source = "from daytona import Daytona\nd = Daytona()\n"
    source += "def run():\n" + indent(workflow, "    ") if function_scope else workflow
    callee = "d.v0.stop" if "d.v0.stop" in body else "stop"
    # When generating guidance for the workflow.
    result = suggest(source)
    # Then its persistent-only calls are identified before suggesting migration.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `box`: these calls require a different lifecycle workflow in SDK 2.x; "
        "keep this workflow on .v0: " + callee
    ]


@pytest.mark.parametrize(
    "body",
    [
        "def later(box: Sandbox):\n    box.stop()\n",
        "def later():\n    box: Sandbox = other\n    box.stop()\n",
        "def later(box: Sandbox):\n    def deeper():\n        box.stop()\n",
        "later = lambda box: box.stop()\n",
        "def later(box):\n    d.v0.stop(box)\n",
        "later = lambda box: d.v0.stop(box)\n",
    ],
)
def test_excludes_deferred_calls_when_body_shadows_sandbox(body: str) -> None:
    # Given a same-named parameter or local that is not the captured sandbox.
    source = (
        "from daytona import Daytona\nfrom daytona.v0 import Sandbox\nd = Daytona()\n"
        "box = d.v0.create()\n" + body
    )
    # When generating guidance for the outer workflow.
    result = suggest(source)
    # Then unrelated deferred calls cannot mark this workflow persistent.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `box`: move this sandbox to v2 as a unit"
    ]


def test_reuses_module_parse_when_guidance_has_multiple_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given analyzed source containing several independent sandbox workflows.
    source = "from daytona import Daytona\nd = Daytona()\n" + "".join(
        f"box{i} = d.v0.create()\nbox{i}.stop()\n" for i in range(3)
    )
    flow = analyzed(source)
    parse = ast.parse
    module_parses: list[str] = []

    def counted_parse(text: str) -> ast.Module:
        if text == source:
            module_parses.append(text)
        return parse(text)

    # When both method lists and persistence checks run for every workflow.
    with monkeypatch.context() as patch:
        patch.setattr(ast, "parse", counted_parse)
        result = guidance(flow, suggestion_mapping())
    # Then the module is parsed at most once, independent of workflow count.
    assert len([item for item in result.items if item["kind"] == "workflow"]) == 3
    assert len(module_parses) <= 1
