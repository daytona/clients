# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Service aliases and annotation proofs retain actionable migration guidance."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate
from test_service_suggestion_ownership import suggest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "annotation,method,form",
    [
        ("CodeInterpreter", "list_contexts", "sandbox.interpreter.list_contexts()"),
        ("Process", "list_pty_sessions", "sandbox.pty.list()"),
    ],
)
def test_manual_owner_when_service_moves(
    annotation: str, method: str, form: str
) -> None:
    # Given a service with no sandbox owner and a different v2 location.
    source = f"from daytona import {annotation}\ndef run(service: {annotation}):\n    service.{method}()\n"
    # When requesting v2 guidance.
    result = suggest(source)
    # Then a manual item names the sandbox-based form instead of inventing a service path.
    manual = [item for item in result.items if item["kind"] == "fixme"]
    assert [item["message"] for item in manual] == [
        "move this call to the owning sandbox: " + form
    ]
    assert "# FIXME(daytona-upgrade): " + manual[0]["message"] in result.source
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("consumer", ["factory()", "saved"])
def test_manual_call_when_local_wraps_decorator_drops_annotation(consumer: str) -> None:
    # Given an unknown local decorator using wraps, which is not proof of its behavior.
    source = (
        "from daytona import Daytona\nfrom functools import wraps\n"
        "def transparent(fn):\n    @wraps(fn)\n    def wrapper(*args, **kwargs):\n"
        "        return fn(*args, **kwargs)\n    return wrapper\n"
        "@transparent\ndef factory() -> Daytona: return Daytona()\n"
        f"saved = factory()\n{consumer}.create()\n"
    )
    # When upgrading a consumer whose name does not resemble a client.
    result = migrate(source)
    # Then the unproven call is manual, never silently left to the runtime guard.
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "# FIXME(daytona-upgrade):" in result.source


@pytest.mark.parametrize(
    "operations,methods",
    [
        ("alias = sb\nalias.set_labels({})", "set_labels"),
        ("alias = sb\nfiles = alias.fs\nfiles.list_files('.')", "fs.list_files"),
        ("files = sb.fs\nother = files\nother.list_files('.')", "fs.list_files"),
        ("alias = sb\nclient.stop(alias)", "client.stop"),
    ],
)
def test_workflow_members_when_sandbox_or_service_is_aliased(
    operations: str, methods: str
) -> None:
    # Given a sandbox workflow accessed through proven aliases.
    source = (
        "from daytona import Daytona\nclient = Daytona()\nsb = client.create()\n"
        + operations
        + "\n"
    )
    # When rendering the workflow header.
    result = suggest(source)
    # Then alias calls appear in the same workflow as direct calls.
    assert [item["message"] for item in result.items if item["kind"] == "workflow"] == [
        "workflow `sb`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: client.stop"
        if methods == "client.stop"
        else "workflow `sb`: move these v0 calls on it to v2 together: " + methods
    ]


def test_manual_call_when_decorated_factory_is_imported() -> None:
    # Given an unproven decorated client factory imported without a local SDK import.
    mapping = load_mapping()
    sources = {
        "factory": "from daytona import Daytona\n@unknown\ndef make() -> Daytona: return Daytona()\n",
        "app": "from factory import make\nmake().create()\n",
    }
    # When the consumer is upgraded using the project index.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then the dropped annotation remains manual across module boundaries.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "make().v0.create()" not in result.source


@pytest.mark.parametrize(
    "operations",
    [
        "alias = sb\nalias = other\nalias.fs.list_files('.')",
        "alias = sb\nif flag: alias = other\nalias.fs.list_files('.')",
        "files = sb.fs\nfiles = other.fs\nfiles.list_files('.')",
        "alias = sb\nclient.stop(alias.id)",
    ],
)
def test_workflow_excludes_calls_when_alias_is_unrelated(operations: str) -> None:
    # Given a different sandbox, an ambiguous alias, or a property of the alias.
    source = (
        "from daytona import Daytona\nclient = Daytona()\nsb = client.create()\n"
        "other = client.create()\n" + operations + "\n"
    )
    # When workflow membership is rendered.
    result = suggest(source)
    # Then another sandbox's work is not attributed to sb.
    workflow = next(item for item in result.items if item["kind"] == "workflow")
    assert workflow["message"] == "workflow `sb`: move this sandbox to v2 as a unit"


@pytest.mark.parametrize(
    "service",
    [
        ("code_interpreter", "list_contexts", "sb.interpreter.list_contexts()"),
        ("process", "list_pty_sessions", "sb.pty.list()"),
    ],
)
def test_owner_is_kept_when_moved_service_has_proven_sandbox(
    service: tuple[str, str, str],
) -> None:
    # Given a service with a known sandbox, unlike the ownerless manual case.
    attribute, method, form = service
    source = f"from daytona import Sandbox\ndef run(sb: Sandbox):\n    service = sb.{attribute}\n    service.{method}()\n"
    # When generating and regenerating guidance.
    result = suggest(source)
    # Then proven ownership remains automatic and guidance is stable on rerun.
    assert not any(item["kind"] == "fixme" for item in result.items)
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == [form]
    assert suggest(result.source).source == result.source


def test_alias_remains_owner_when_original_sandbox_is_rebound() -> None:
    # Given an alias which still proves its own sandbox after the first name is rebound.
    source = (
        "from daytona import Sandbox\ndef run(sb: Sandbox, other: Sandbox):\n"
        "    alias = sb\n    sb = other\n    alias.code_interpreter.list_contexts()\n"
    )
    # When requesting moved-service guidance.
    result = suggest(source)
    # Then the still-valid alias is a usable owner, not a new manual placeholder.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == ["alias.interpreter.list_contexts()"]
    assert not any(item["kind"] == "fixme" for item in result.items)
