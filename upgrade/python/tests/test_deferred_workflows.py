# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for eager iterables and deferred local sandbox aliases."""

from __future__ import annotations

import ast
from pathlib import Path
from textwrap import indent

import pytest
from test_workflow_binding_lifetimes import analyzed

from daytona_upgrade import cli
from daytona_upgrade._workflow_scopes import WorkflowScopes
from daytona_upgrade.models import load_mapping
from daytona_upgrade.suggestions import guidance, workflow_methods


@pytest.mark.parametrize("target", ["x", "box"])
@pytest.mark.parametrize(
    "expression",
    [
        "[{target} for {target} in box.process.list()]",
        "({target} for {target} in box.process.list())",
        "{{{target} for {target} in box.process.list()}}",
        "{{{target}: {target} for {target} in box.process.list()}}",
    ],
)
def test_first_iterable_uses_enclosing_scope_when_comprehension_binds_target(
    expression: str, target: str
) -> None:
    # Given a first iterable evaluated before the comprehension binds its target.
    module = ast.parse(expression.format(target=target))
    scopes = WorkflowScopes(module)
    call = next(node for node in ast.walk(module) if isinstance(node, ast.Call))
    # When locating the call's evaluation scope.
    scope = scopes.scope(call)
    # Then even a generator's first iterable executes in the enclosing module.
    assert scope is module


@pytest.mark.parametrize("target", ["x", "box"])
@pytest.mark.parametrize(
    "expression",
    [
        "[{target} for {target} in box.process.list()]",
        "({target} for {target} in box.process.list())",
    ],
)
def test_eager_iterable_belongs_only_to_current_workflow_when_binding_is_reassigned(
    expression: str, target: str
) -> None:
    # Given two generations separated by an eagerly evaluated first iterable.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\n"
        "box = api.v0.create()\n"
        + expression.format(target=target)
        + "\nbox = api.v0.create()\n"
    )
    producers = [call for call in flow.calls if call.method == "create"]
    # When collecting the workflows on each side of the comprehension.
    members = [workflow_methods(producer, flow) for producer in producers]
    # Then the first iterable neither captures a later binding nor loses the current one.
    assert members == [["process.list"], []]


@pytest.mark.parametrize(
    "expression",
    [
        "(box.stop() for x in others)",
        "(x for x in others for y in box.stop())",
        "(x for x in others if box.stop())",
    ],
)
def test_generator_body_remains_deferred_when_call_is_not_in_first_iterable(
    expression: str,
) -> None:
    # Given a generator whose body executes only when it is consumed.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\n"
        + expression
        + "\nbox = api.v0.create()\n"
    )
    producer = next(call for call in flow.calls if call.method == "create")
    # When collecting calls which capture the produced binding.
    members = workflow_methods(producer, flow)
    # Then skipping the first iterable's scope does not skip other generator code.
    assert members == ["stop"]


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize(
    "body,member",
    [
        ("def cleanup():\n    alias = box\n    alias.stop()\n", "stop"),
        ("async def cleanup():\n    alias = box\n    alias.stop()\n", "stop"),
        ("def cleanup():\n    alias: Sandbox = box\n    alias.stop()\n", "stop"),
        (
            "def cleanup():\n    first = box\n    alias = first\n    alias.stop()\n",
            "stop",
        ),
        (
            "def cleanup():\n    alias = box\n    def inner():\n        alias.stop()\n",
            "stop",
        ),
        ("def cleanup():\n    alias = box\n    api.v0.stop(alias)\n", "api.v0.stop"),
    ],
)
def test_persistent_header_keeps_v0_when_earlier_helper_uses_local_alias(
    body: str, member: str, local: bool
) -> None:
    # Given a helper defined before its captured sandbox is produced.
    workflow = body + "box = api.v0.create()\n"
    source = "from daytona import Daytona\napi = Daytona()\n"
    source += "def run():\n" + indent(workflow, "    ") if local else workflow
    flow = analyzed(source)
    # When generating workflow guidance using the shipped mapping.
    result = guidance(flow, load_mapping())
    # Then the aliased stop is a persistence dependency, not permission to migrate.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `box`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: "
        + member
    ]


@pytest.mark.parametrize(
    "body",
    [
        "def cleanup(box):\n    alias = box\n    alias.stop()\n",
        "def cleanup():\n    alias = box\n    alias = other\n    alias.stop()\n",
        "def cleanup():\n    alias.stop()\n    alias = box\n",
        "def cleanup():\n    alias = box\n    def inner(alias):\n        alias.stop()\n",
        "def cleanup():\n    alias = box\n    box = other\n    alias.stop()\n",
        "def cleanup():\n    alias = other\n    alias.stop()\n",
    ],
)
def test_workflow_excludes_alias_call_when_it_does_not_capture_sandbox(
    body: str,
) -> None:
    # Given a local alias whose source, lifetime, or lexical owner is unrelated.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\n"
        + body
        + "box = api.v0.create()\n"
    )
    producer = next(call for call in flow.calls if call.method == "create")
    # When collecting members through the suggestion-only alias index.
    members = workflow_methods(producer, flow)
    # Then no sandbox ownership is invented from the alias's spelling.
    assert members == []


def test_cli_keeps_persistent_workflow_on_v0_when_helper_captures_local_alias(
    tmp_path: Path,
) -> None:
    # Given a real source file with the helper declared before its producer.
    path = tmp_path / "app.py"
    path.write_text(
        "from daytona import Daytona\napi = Daytona()\n"
        "def cleanup():\n    alias = box\n    alias.stop()\n"
        "box = api.v0.create()\n"
    )
    # When running suggestion mode through the public CLI.
    status = cli.main([str(tmp_path), "--suggest-v2", "--force", "--yes"])
    # Then the written header retains v0 and names the aliased persistence call.
    assert status == 0
    assert (
        "# daytona-v2: workflow `box`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: stop\n"
        in path.read_text()
    )
