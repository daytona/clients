# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Guidance changes comments only, with structured, receiver-specific reports."""

from __future__ import annotations

import ast
from dataclasses import replace

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import (
    Mapping,
    Mode,
    SourceResult,
    Suggestion,
    TransformOptions,
    load_mapping,
)
from daytona_upgrade.transform import transform_source


def suggestion_mapping() -> Mapping:
    """Use a small explicit mapping independent of the changing public guide."""
    return replace(
        load_mapping(),
        docs="https://example.test/migrate",
        data_traps=("IDs do not carry over.",),
        suggestions=(
            Suggestion(
                "client.create",
                "client",
                "create",
                '{client}.v2.create("python:3.12")',
                "differs",
                ("Choose an image.",),
                "client-create",
                True,
            ),
            Suggestion(
                "sandbox.fs.list_files",
                "sandbox.fs",
                "list_files",
                "{sandbox}.fs.list(path)",
                "direct",
                (),
                "fs-list",
                False,
            ),
            Suggestion(
                "sandbox.legacy",
                "sandbox",
                "legacy",
                None,
                "none",
                ("Review manually.",),
                "sandbox-legacy",
                False,
            ),
        ),
    )


def suggest(source: str, mode: Mode = "suggest-v2") -> SourceResult:
    mapping = suggestion_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, mode),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


def test_guidance_when_proven_call_is_found() -> None:
    # Given an existing v0 workload with meaningful argument expressions.
    source = (
        "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create(settings)\n"
    )
    # When asking for v2 guidance.
    result = suggest(source)
    # Then executable syntax is identical and machine-readable guidance is precise.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    entries = [item["suggestion"] for item in result.items if "suggestion" in item]
    assert len(entries) == 1
    assert {key: value for key, value in entries[0].items() if key != "notes"} == {
        "id": "client.create",
        "receiver": "client",
        "v0": "api.v0.create",
        "v2": 'api.v2.create("python:3.12")',
        "category": "differs",
        "docs": "https://example.test/migrate#client-create",
    }
    assert entries[0]["notes"] == list(suggestion_mapping().suggestions[0].notes)
    assert [(item["line"], item["column"]) for item in result.items] == [(3, 7), (3, 7)]


def test_nested_service_when_sandbox_is_produced() -> None:
    # Given a sandbox produced by create, followed by filesystem work.
    source = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\nbox.fs.list_files('.')\n"
    # When producing guidance.
    result = suggest(source)
    # Then service suggestions substitute the actual sandbox binding.
    entries = [item["suggestion"] for item in result.items if "suggestion" in item]
    assert [entry["id"] for entry in entries] == [
        "client.create",
        "sandbox.fs.list_files",
    ]
    assert entries[1]["v2"] == "box.fs.list(path)"
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_no_equivalent_when_mapping_explicitly_has_none() -> None:
    # Given a mapped receiver with a deliberately unsupported operation.
    source = (
        "from daytona.v0 import Sandbox\ndef run(box: Sandbox):\n    box.legacy()\n"
    )
    # When producing guidance.
    result = suggest(source)
    # Then the report preserves null rather than inventing a v2 call.
    entries = [item["suggestion"] for item in result.items if "suggestion" in item]
    assert len(entries) == 1
    assert entries[0]["v2"] is None
    assert entries[0]["category"] == "none"
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_guidance_when_receiver_is_unknown() -> None:
    # Given an unannotated parameter whose name alone suggests Daytona.
    source = "from daytona import Daytona\ndef run(client):\n    client.create()\n"
    # When requesting suggestions.
    result = suggest(source)
    # Then no false v2 advice is emitted.
    assert result.source == source
    assert result.items == ()


def test_suggestion_rerun_when_comments_already_exist() -> None:
    # Given the exact output of a prior suggestion run.
    source = suggest(
        "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n"
    ).source
    # When rerunning suggestions.
    result = suggest(source)
    # Then comments are replaced rather than accumulated.
    assert result.source == source


def test_removal_when_user_comments_are_interleaved() -> None:
    # Given tagged tool lines and nearby comments owned by the user.
    source = "# keep this\n# daytona-v2: obsolete\nfrom daytona import Daytona\n# daytona-v2 is a user note\napi = Daytona()\ndef run():\n    # daytona-v2: old guidance\n    # preserve this too\n    api.v0.create()\n"
    # When removing suggestions.
    result = suggest(source, "remove-suggestions")
    # Then only exact tagged comment lines are removed, including module headers.
    assert (
        result.source
        == "# keep this\nfrom daytona import Daytona\n# daytona-v2 is a user note\napi = Daytona()\ndef run():\n    # preserve this too\n    api.v0.create()\n"
    )


@pytest.mark.parametrize("producer", ["create", "get"])
def test_workflow_when_multiple_sandboxes_share_method_names(producer: str) -> None:
    # Given independent sandbox workflows.
    mapping = suggestion_mapping()
    mapping = replace(
        mapping,
        suggestions=(
            *mapping.suggestions,
            replace(
                mapping.suggestions[0],
                id="client.get",
                method="get",
                anchor="client-get",
            ),
        ),
    )
    source = f"from daytona import Daytona\napi = Daytona()\nfirst = api.v0.{producer}('one')\nsecond = api.v0.create()\nfirst.fs.list_files('.')\nsecond.legacy()\n"
    # When generating workflow headers.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then headers describe only calls belonging to each produced sandbox.
    workflows = [item for item in result.items if item["kind"] == "workflow"]
    assert len(workflows) == 2
    first_header = workflows[0]["message"]
    assert "`first`" in first_header and "fs.list_files" in first_header
    assert "legacy" not in first_header


def test_workflow_includes_client_calls_that_receive_the_sandbox() -> None:
    # Given a sandbox whose later calls go both through it and through the client.
    source = (
        "from daytona import Daytona\n"
        "daytona = Daytona()\n"
        "sandbox = daytona.v0.create()\n"
        "other = daytona.v0.create()\n"
        "sandbox.set_labels({'a': 'b'})\n"
        "daytona.v0.stop(sandbox)\n"
        "daytona.v0.stop(other)\n"
        "daytona.v0.start(sandbox=sandbox)\n"
        "daytona.v0.stop(sandbox)\n"
        "daytona.v0.delete(sandbox)\n"
    )
    mapping = load_mapping()
    # When generating suggestions with the real mapping.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then each sandbox workflow stays on v0 when its own client calls require it.
    headers = {item["message"] for item in result.items if item["kind"] == "workflow"}
    assert (
        "workflow `sandbox`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: daytona.v0.stop, daytona.v0.start"
    ) in headers
    assert (
        "workflow `other`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: daytona.v0.stop"
        in headers
    )


@pytest.mark.parametrize(
    ("method", "call"),
    [
        # Each 0.x method's own call shape (daytona 0.x _async/git.py).
        (
            "clone",
            "clone('https://example.test/r.git', '/repo', branch='main', request_timeout=0)",
        ),
        ("pull", "pull('/repo', username='u', password='p', request_timeout=0)"),
        (
            "push",
            "push('/repo', username='u', password='p', set_upstream=True, request_timeout=0)",
        ),
    ],
)
def test_git_remote_calls_carry_request_timeout_into_the_v2_timeout(
    method: str, call: str
) -> None:
    # Given a 0.x git call with request_timeout=0 (no limit).
    source = (
        "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n"
        f"box.git.{call}\n"
    )
    # When producing guidance from the published mapping.
    mapping = load_mapping()
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the v2 form passes the deadline on, and 0 keeps meaning no limit.
    entry = next(
        item["suggestion"]
        for item in result.items
        if "suggestion" in item and item["suggestion"]["id"] == f"sandbox.git.{method}"
    )
    form = entry["v2"]
    assert form is not None and form.endswith(", timeout=request_timeout)")
    assert any(
        "request_timeout=0 (no limit) becomes timeout=0" in note
        for note in entry["notes"]
    )
    assert "timeout=request_timeout" in result.source
