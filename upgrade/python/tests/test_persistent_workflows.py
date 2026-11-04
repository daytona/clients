# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Persistent-only workflows must not recommend an unavailable v2 migration."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "operation",
    [
        "sandbox.stop()",
        "sandbox.start()",
        "sandbox.pause()",
        "sandbox.archive()",
        "sandbox.set_autostop_interval(10)",
        "sandbox.set_auto_pause_interval(10)",
        "sandbox.set_auto_archive_interval(10)",
        "client.stop(sandbox)",
        "client.start(sandbox)",
    ],
)
def test_keeps_workflow_on_v0_when_call_requires_persistence(operation: str) -> None:
    # Given a create workflow containing a persistent-only lifecycle operation.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        f"sandbox = client.v0.create()\n{operation}\n"
    )
    mapping = load_mapping()
    # When suggesting a v2 migration.
    result = transform_source(
        source,
        TransformOptions(mapping, mode="suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then both the source header and structured report recommend staying on v0.
    message = (
        "workflow `sandbox`: these calls require a different lifecycle workflow in SDK 2.x; "
        "keep this workflow on .v0: " + operation.split("(")[0].removeprefix("sandbox.")
    )
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        message
    ]
    assert "# daytona-v2: " + message in result.source


@pytest.mark.parametrize("service", ["computer_use", "recording"])
def test_nested_service_lifecycle_does_not_require_persistence(service: str) -> None:
    # Given a v2-compatible nested service workflow with a lifecycle-like method.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        f"sandbox = client.v0.create()\nsandbox.{service}.start()\n"
        f"sandbox.{service}.stop()\n"
    )
    mapping = load_mapping()
    # When suggesting a v2 migration.
    result = transform_source(
        source,
        TransformOptions(mapping, mode="suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then nested service start/stop never receives the persistent-only warning.
    expected = (
        "workflow `sandbox`: move these v0 calls on it to v2 together: "
        "computer_use.start, computer_use.stop"
        if service == "computer_use"
        else "workflow `sandbox`: move this sandbox to v2 as a unit"
    )
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        expected
    ]


def test_unlisted_sandbox_method_does_not_require_persistence() -> None:
    # Given a sandbox method outside the explicit persistent lifecycle set.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        "sandbox = client.v0.create()\nsandbox.set_auto_delete_interval(10)\n"
    )
    mapping = load_mapping()
    # When suggesting a v2 migration.
    result = transform_source(
        source,
        TransformOptions(mapping, mode="suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the method does not trigger the persistent-only warning.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `sandbox`: move these v0 calls on it to v2 together: "
        "set_auto_delete_interval"
    ]


@pytest.mark.parametrize("persistent_first", [True, False])
@pytest.mark.parametrize("function_scope", [True, False])
def test_scopes_persistence_when_sandbox_binding_is_reassigned(
    persistent_first: bool, function_scope: bool
) -> None:
    # Given two workflows sharing a name, only one with persistent calls.
    persistent = "sandbox.stop()\nsandbox.start()\n"
    migratable = 'sandbox.process.exec("second")\nsandbox.delete()\n'
    blocks = [persistent, migratable] if persistent_first else [migratable, persistent]
    body = "".join("sandbox = client.v0.create()\n" + block for block in blocks)
    source = "from daytona import Daytona\nclient = Daytona()\n"
    source += (
        "def run():\n" + "".join("    " + line for line in body.splitlines(True))
        if function_scope
        else body
    )
    mapping = load_mapping()
    # When suggesting both workflows.
    result = transform_source(
        source,
        TransformOptions(mapping, mode="suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then each report retains its own ordered calls and migration decision.
    persistent_header = (
        "workflow `sandbox`: these calls require a different lifecycle workflow in SDK 2.x; "
        "keep this workflow on .v0: stop, start"
    )
    migratable_header = "workflow `sandbox`: move these v0 calls on it to v2 together: process.exec, delete"
    expected = (
        [persistent_header, migratable_header]
        if persistent_first
        else [migratable_header, persistent_header]
    )
    assert [
        item["message"] for item in result.items if item["kind"] == "workflow"
    ] == expected
