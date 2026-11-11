# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Multiline and deferred calls retain parseable workflow guidance."""

from __future__ import annotations

import ast
import io
import json
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path
from textwrap import indent

import pytest
from test_suggestions import suggestion_mapping
from test_workflow_binding_lifetimes import analyzed

from daytona_upgrade import cli
from daytona_upgrade.comments import leading_comments
from daytona_upgrade.lexical import single_line
from daytona_upgrade.models import load_mapping
from daytona_upgrade.suggestions import guidance, workflow_methods


@pytest.mark.parametrize(
    "operation",
    [
        callee.format(method=method) + "(box)"
        for method in ("stop", "start", "delete")
        for callee in (
            "api.v0 " + "\\" + "\n    .{method}",
            "(\n    api\n    .v0\n    .{method}\n)",
            "(api  # client\n .v0).{method}",
        )
    ],
)
@pytest.mark.parametrize("json_report", [False, True])
def test_suggests_both_files_when_client_callee_spans_lines(
    tmp_path: Path, operation: str, json_report: bool
) -> None:
    # Given one multiline workflow plus a normal file in the same project.
    prefix = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n"
    (tmp_path / "bad.py").write_text(prefix + operation + "\n")
    (tmp_path / "good.py").write_text(prefix + "box.fs.list_files('.')\n")
    arguments = [str(tmp_path), "--suggest-v2", "--yes", "--force"]
    if json_report:
        arguments += ["--json"]
    # When running the public CLI across the entire project.
    output = io.StringIO()
    with redirect_stdout(output):
        status = cli.main(arguments)
    # Then neither file loses its suggestions to a comment-construction error.
    assert status == 0
    if json_report:
        report = json.loads(output.getvalue())
        assert {item["file"] for item in report["items"]} == {"bad.py", "good.py"}
    else:
        for filename in ("bad.py", "good.py"):
            assert "# daytona-v2:" in (tmp_path / filename).read_text()


@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n"])
def test_comment_stays_parseable_when_message_contains_newlines(separator: str) -> None:
    # Given source-derived text containing a physical line separator.
    message = "daytona-v2: " + separator.join(["first", "second"])
    # When creating a standalone guidance comment.
    lines = leading_comments([], [message], ("suggest-v2", ""))
    # Then it is one valid CST comment rather than executable injected text.
    assert len(lines) == 1
    assert lines[0].comment is not None
    assert lines[0].comment.value == "# daytona-v2: first second"


def test_json_message_matches_multiline_receiver_comment(tmp_path: Path) -> None:
    # Given a client receiver split across physical lines.
    source = (
        "from daytona import Daytona\n"
        "api = Daytona()\n"
        "(\n    registry.daytona_client\n).create()\n"
    )
    source_file = tmp_path / "app.py"
    source_file.write_text(source)
    output = io.StringIO()
    # When running the CLI in JSON mode and inspecting its changed source.
    with redirect_stdout(output):
        status = cli.main([str(tmp_path), "--yes", "--force", "--json"])
    report = json.loads(output.getvalue())
    item = next(item for item in report["items"] if item["kind"] == "fixme")
    comment = next(
        line
        for line in source_file.read_text().splitlines()
        if "FIXME(daytona-upgrade):" in line
    )
    # Then the JSON report and written comment carry the same message.
    assert status == 0
    assert item["message"] == comment.removeprefix("# FIXME(daytona-upgrade): ")


def test_single_line_removes_cr_only_continuation() -> None:
    # Given a source expression continued with a bare CR line ending.
    text = "api.v0 \\\r    .stop"
    # When rendering it as a one-line header.
    rendered = single_line(text)
    # Then the continuation marker and line break are both removed.
    assert rendered == "api.v0 .stop"


@pytest.mark.parametrize("before", [False, True])
@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        ("def cleanup():\n    box.stop()\n", "stop"),
        ("async def cleanup():\n    box.stop()\n", "stop"),
        ("cleanup = lambda: box.stop()\n", "stop"),
        ("def cleanup():\n    api.v0.stop(box)\n", "api.v0.stop"),
        ("def cleanup():\n    box.fs.list_files('.')\n", "fs.list_files"),
        ("def cleanup():\n    def inner():\n        box.stop()\n", "stop"),
    ],
)
def test_includes_captured_call_when_closure_surrounds_producer(
    before: bool, local: bool, case: tuple[str, str]
) -> None:
    # Given a deferred call on a binding captured before or after its producer.
    body, member = case
    producer = "box = api.v0.create()\n"
    workflow = body + producer if before else producer + body
    source = "from daytona import Daytona\napi = Daytona()\n"
    source += "def run():\n" + indent(workflow, "    ") if local else workflow
    flow = analyzed(source)
    producer_call = next(call for call in flow.calls if call.method == "create")
    # When looking up workflow membership and generating real-mapping guidance.
    members = workflow_methods(producer_call, flow)
    result = guidance(flow, load_mapping())
    # Then the captured call belongs to the workflow and gets its own note.
    assert members == [member]
    call_items = [
        item
        for item in result.items
        if "suggestion" in item and item["kind"] != "workflow"
    ]
    assert len(call_items) == 2
    assert {item["suggestion"]["receiver"] for item in call_items} == {
        "client",
        "sandbox.fs"
        if member == "fs.list_files"
        else "client"
        if member.startswith("api")
        else "sandbox",
    }
    headers = [item["message"] for item in result.items if item["kind"] == "workflow"]
    assert len(headers) == 1
    assert ("keep this workflow on .v0" in headers[0]) == (member != "fs.list_files")


@pytest.mark.parametrize(
    "body",
    [
        "def cleanup(box):\n    box.stop()\n",
        "def cleanup():\n    box.stop()\n    box = other\n",
        "cleanup = lambda box: box.stop()\n",
        "def cleanup(value=box.stop()):\n    pass\n",
        "class Cleanup:\n    box.stop()\n",
        "def cleanup():\n    [box.stop() for box in others]\n",
    ],
)
def test_excludes_call_when_earlier_body_does_not_capture_binding(body: str) -> None:
    # Given a shadowed binding or an expression evaluated before the producer.
    source = (
        "from daytona import Daytona\napi = Daytona()\n"
        + body
        + "box = api.v0.create()\n"
    )
    flow = analyzed(source)
    producer = next(call for call in flow.calls if call.method == "create")
    # When determining membership of the produced workflow.
    members = workflow_methods(producer, flow)
    # Then eager expressions and other bindings are excluded.
    assert members == []


@pytest.mark.parametrize(
    "source",
    [
        "def cleanup():\n    box.stop()\nbox = unknown.create()\n",
        "class Runner:\n    box = api.v0.create()\n    def cleanup(self):\n        box.stop()\n",
        "def run():\n    box = api.v0.create()\n\ndef cleanup():\n    box.stop()\n",
    ],
)
def test_preserves_unknown_call_when_no_lexical_sandbox_capture_exists(
    source: str,
) -> None:
    # Given either an unproven producer or a producer in an unrelated lexical scope.
    flow = analyzed("from daytona import Daytona\napi = Daytona()\n" + source)
    # When suggestion-only capture recovery runs.
    result = guidance(flow, load_mapping())
    # Then the unrelated stop call never acquires a fabricated SDK receiver proof.
    assert not any(
        item["suggestion"]["id"] == "sandbox.stop"
        for item in result.items
        if "suggestion" in item and item["kind"] != "workflow"
    )


def test_captures_follow_binding_when_direct_calls_follow_rebinding_windows() -> None:
    # Given a callback which can run with either generation of the same binding.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\n"
        "def cleanup():\n    box.stop()\n"
        "box = api.v0.create()\nbox.fs.list_files('first')\n"
        "box = api.v0.create()\nbox.process.exec('second')\n"
    )
    producers = [call for call in flow.calls if call.method == "create"]
    # When collecting both generations' workflow members.
    members = [workflow_methods(producer, flow) for producer in producers]
    # Then deferred binding use counts for both; sequential calls remain separated.
    assert members == [["stop", "fs.list_files"], ["stop", "process.exec"]]


def test_module_walks_stay_bounded_when_workflow_count_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given many workflows in one already analyzed module.
    source = "from daytona import Daytona\napi = Daytona()\n" + "".join(
        f"box{i} = api.v0.create()\nbox{i}.fs.list_files('.')\n" for i in range(20)
    )
    flow = analyzed(source)
    walk = ast.walk
    walks: list[ast.AST] = []

    def counted_walk(node: ast.AST) -> Iterator[ast.AST]:
        if node is flow.module:
            walks.append(node)
        return walk(node)

    # When generating both membership and persistence guidance for every workflow.
    with monkeypatch.context() as patch:
        patch.setattr(ast, "walk", counted_walk)
        result = guidance(flow, suggestion_mapping())
    # Then the number of whole-module scans is independent of workflow count.
    assert len([item for item in result.items if item["kind"] == "workflow"]) == 20
    assert len(walks) <= 6
