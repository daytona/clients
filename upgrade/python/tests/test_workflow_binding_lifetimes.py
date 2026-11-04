# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Workflow binding lifetimes preserve call-site identity."""

from __future__ import annotations

import sys

import pytest
from test_suggestions import suggest, suggestion_mapping

from daytona_upgrade.flow import Flow, SourceContext
from daytona_upgrade.resolution import ProjectIndex, Resolver
from daytona_upgrade.suggestions import workflow_methods


def analyzed(source: str) -> Flow:
    mapping = suggestion_mapping()
    index = ProjectIndex({})
    flow = Flow(Resolver(mapping, index), SourceContext("app.py", "app", index))
    flow.analyze(source)
    return flow


@pytest.mark.parametrize("member", ["box.fs.list_files('.')", "api.v0.stop(box)"])
def test_workflow_members_when_binding_is_reassigned(member: str) -> None:
    # Given two sandbox generations with identical variable spelling.
    source = f"from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\nbox = api.v0.create()\n{member}\n"
    flow = analyzed(source)
    producers = [call for call in flow.calls if call.method == "create"]
    # When collecting each generation's workflow.
    members = [workflow_methods(producer, flow) for producer in producers]
    # Then only the second producer owns the later call.
    assert members == [
        [],
        ["fs.list_files" if member.startswith("box") else "api.v0.stop"],
    ]


def test_workflow_members_when_call_precedes_same_line_producer() -> None:
    # Given an existing sandbox used before a second producer on the same line.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\nbox.fs.list_files('.'); box = api.v0.create()\n"
    )
    producer = [call for call in flow.calls if call.method == "create"][-1]
    # When collecting the later producer's members.
    members = workflow_methods(producer, flow)
    # Then the earlier call is excluded by its column, not just its row.
    assert members == []


@pytest.mark.parametrize(
    "rebinding",
    [
        "box: object = other",
        "(box := other)",
        "del box",
        "if flag:\n    box = other",
        "from other import value as box",
        "from other import *",
        "import other as box",
        "def box():\n    pass",
        "class box:\n    pass",
        "try:\n    risky()\nexcept Error as box:\n    pass",
        pytest.param(
            "match other:\n    case box:\n        pass",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 10), reason="match syntax requires Python 3.10"
            ),
        ),
    ],
)
def test_workflow_members_when_any_binding_write_intervenes(rebinding: str) -> None:
    # Given a possible replacement, deletion, or shadowing of the produced name.
    flow = analyzed(
        f"from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n{rebinding}\napi.v0.stop(box)\n"
    )
    producer = next(call for call in flow.calls if call.method == "create")
    # When collecting workflow members conservatively across that write.
    members = workflow_methods(producer, flow)
    # Then spelling alone cannot attach the unrelated value to the sandbox.
    assert members == []


def test_workflow_members_when_parent_object_is_rebound() -> None:
    # Given a sandbox attribute whose owner is subsequently replaced.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\nowner.box = api.v0.create()\nowner = other\napi.v0.stop(owner.box)\n"
    )
    producer = next(call for call in flow.calls if call.method == "create")
    # When collecting calls after the owner's binding changes.
    members = workflow_methods(producer, flow)
    # Then the former attribute's workflow does not acquire the new owner's value.
    assert members == []


def test_guidance_when_mapping_repeats_on_same_line() -> None:
    # Given two producer call sites with the same mapping ID and physical row.
    source = "from daytona import Daytona\napi = Daytona()\na = api.v0.create(); b = api.v0.create()\n"
    # When emitting structured guidance.
    result = suggest(source)
    # Then both call sites have their own suggestion and workflow entry.
    assert [(item["kind"], item["column"]) for item in result.items] == [
        ("suggestion", 5),
        ("workflow", 5),
        ("suggestion", 26),
        ("workflow", 26),
    ]


def test_workflows_when_same_binding_has_two_assignments_on_one_row() -> None:
    # Given two distinct assignments sharing the row and the variable spelling.
    source = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create(); box = api.v0.create()\n"
    # When workflow headers are grouped by assignment, not only row and name.
    result = suggest(source)
    # Then neither separate sandbox generation loses its structured workflow item.
    workflows = [item for item in result.items if item["kind"] == "workflow"]
    assert len(workflows) == 2
