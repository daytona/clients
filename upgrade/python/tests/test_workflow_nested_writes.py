# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Workflow membership respects lexical writes without losing shared mutations."""

from __future__ import annotations

import pytest
from test_suggestions import suggest


@pytest.mark.parametrize(
    "helper",
    [
        "def helper():\n    box = other",
        "async def helper():\n    box = other",
        "class Helper:\n    box = other",
        "helper = lambda: (box := other)",
        "helpers = [box for box in others]",
    ],
)
def test_workflow_keeps_later_call_when_nested_write_is_local(helper: str) -> None:
    # Given a sandbox and a nested scope with an unrelated local of the same name.
    source = (
        "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n"
        + helper
        + "\nbox.fs.list_files('.')\n"
    )
    # When grouping the outer sandbox's later calls.
    result = suggest(source)
    # Then the local helper store cannot cut off valid outer guidance.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `box`: move these v0 calls on it to v2 together: fs.list_files"
    ]


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
def test_workflow_stops_when_nested_write_explicitly_targets_outer_binding(
    declaration: str,
) -> None:
    # Given a deferred helper which can replace the produced sandbox.
    body = (
        "api = Daytona()\nbox = api.v0.create()\ndef helper():\n    "
        + declaration
        + " box\n    box = other\nhelper()\nbox.fs.list_files('.')\n"
    )
    prefix = "from daytona import Daytona\n"
    source = (
        prefix
        + "def run():\n"
        + "".join("    " + line + "\n" for line in body.splitlines())
        if declaration == "nonlocal"
        else prefix + body
    )
    # When gathering workflow membership across a shared binding mutation.
    result = suggest(source)
    # Then uncertain later uses are excluded rather than assigned to the old sandbox.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `box`: move this sandbox to v2 as a unit"
    ]
