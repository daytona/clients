# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sandbox workflow membership includes client calls on the sandbox argument."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_suggestions import suggest, suggestion_mapping
from test_workflow_binding_lifetimes import analyzed

from daytona_upgrade import cli
from daytona_upgrade.suggestions import workflow_methods


@pytest.mark.parametrize(
    "operation,member",
    [
        ("api.v0.stop(box)", "api.v0.stop"),
        ("api.v0.start(reason, box)", "api.v0.start"),
        ("api.v0.delete(sandbox=box)", "api.v0.delete"),
        ("api.v0.snapshot.delete(box)", "api.v0.snapshot.delete"),
        ("api.v0.volume.get(name, sandbox=box)", "api.v0.volume.get"),
        ("api . v0 .stop(box)", "api . v0 .stop"),
        ("api.v0 " + "\\" + "\n    .stop(box)", "api.v0 .stop"),
        ("api.v0 " + "\\" + "\n    .start(box)", "api.v0 .start"),
        ("api.v0 " + "\\" + "\n    .delete(box)", "api.v0 .delete"),
        ("(\n    api\n    .v0\n    .stop\n)(box)", "api .v0 .stop"),
        ("api.stop(box)", "api.stop"),
        ("Daytona().v0.stop(box)", "Daytona().v0.stop"),
    ],
)
def test_client_call_when_sandbox_is_a_positional_or_keyword_argument(
    operation: str, member: str
) -> None:
    # Given a proven client or service with a direct reference to the produced sandbox.
    source = f"from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n{operation}\n"
    # When the workflow header is generated from real flow analysis.
    result = suggest(source)
    # Then membership preserves the exact callee and lifecycle calls fence migration.
    flow = analyzed(source)
    assert workflow_methods(flow.calls[0], flow) == [member]
    expected = (
        "workflow `box`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: "
        + member
        if member.rsplit(".", 1)[-1].strip() in {"stop", "start"}
        else f"workflow `box`: move these v0 calls on it to v2 together: {member}"
    )
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        expected
    ]
    assert "# daytona-v2: " + expected in result.source


@pytest.mark.parametrize(
    "operation",
    [
        "api.v0.stop(other)",
        "api.v0.stop(box.id)",
        "api.v0.stop('box')",
        "api.v0.stop([box])",
        "api.v2.stop(box)",
        "unproven.stop(box)",
        "def other_scope(box):\n    api.v0.stop(box)",
    ],
)
def test_client_call_when_argument_or_receiver_is_outside_the_workflow(
    operation: str,
) -> None:
    # Given an unrelated, nested, unproven, or different-scope call.
    source = f"from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n{operation}\n"
    # When workflow membership is calculated.
    result = suggest(source)
    # Then the unrelated call does not cause a multi-call workflow header.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `box`: move this sandbox to v2 as a unit"
    ]


def test_workflow_members_when_calls_repeat_and_interleave() -> None:
    # Given repeated client and sandbox calls, interleaved with another sandbox's work.
    source = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\nother = api.v0.create()\nbox.fs.list_files('a')\napi.v0.stop(box)\nbox.fs.list_files('b')\napi.v0.stop(sandbox=box)\napi.v0.start(box)\napi.v0.delete(other)\nbox.process.exec('ls')\napi.v0.snapshot.delete(box)\napi.v0.delete(box)\n"
    # When one workflow is assembled from all call sites.
    result = suggest(source)
    # Then members stay in source order with duplicates removed at first occurrence.
    flow = analyzed(source)
    assert workflow_methods(flow.calls[0], flow) == [
        "fs.list_files",
        "api.v0.stop",
        "api.v0.start",
        "process.exec",
        "api.v0.snapshot.delete",
        "api.v0.delete",
    ]
    messages = [item["message"] for item in result.items if item["kind"] == "workflow"]
    assert messages == [
        "workflow `box`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: api.v0.stop, api.v0.start",
        "workflow `other`: move these v0 calls on it to v2 together: api.v0.delete",
    ]


def test_client_call_when_sandbox_is_bound_to_an_attribute() -> None:
    # Given an existing supported sandbox binding on a class instance.
    source = "from daytona import Daytona\napi = Daytona()\nclass Runner:\n    def run(self):\n        self.box = api.v0.create()\n        api.v0.stop(sandbox=self.box)\n"
    # When the workflow includes client calls taking that same binding.
    result = suggest(source)
    # Then attribute bindings behave like simple local variable bindings.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `self.box`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: api.v0.stop"
    ]


def test_json_workflow_when_client_calls_receive_the_sandbox(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the same lifecycle project used by the byte-exact fixture test.
    original = (
        Path(__file__).parent
        / "fixtures/suggest-v2-workflow-client-arguments/input/app.py"
    ).read_text()
    path = tmp_path / "app.py"
    path.write_text(original)
    monkeypatch.setattr(cli, "load_mapping", suggestion_mapping)
    # When requesting the public JSON report without modifying files.
    status = cli.main([str(tmp_path), "--suggest-v2", "--json", "--force", "--yes"])
    # Then the report's workflow membership matches the written-comment contract.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [
        item["message"] for item in report["items"] if item["kind"] == "workflow"
    ] == [
        "workflow `sandbox`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: daytona.v0.stop, daytona.v0.start"
    ]
    assert path.read_text() == original
