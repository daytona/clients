# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Service guidance retains proven sandbox ownership without inventing nesting."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def suggest(source: str) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize(
    "service",
    [
        ("fs", "list_files", "fs.list(path, depth=depth)"),
        ("git", "status", "git.status(path)"),
        (
            "process",
            "exec",
            "process.run(command, cwd=cwd, env=env, execution_timeout=timeout)",
        ),
    ],
)
@pytest.mark.parametrize("alias_chain", ["", "service = first\n    "])
def test_sandbox_owner_when_service_is_aliased(
    service: tuple[str, str, str], alias_chain: str
) -> None:
    # Given a service alias, optionally reached through another alias.
    attribute, method, form = service
    binding = "first" if alias_chain else "service"
    source = (
        "from daytona import Sandbox\n"
        "def run(sb: Sandbox):\n"
        f"    {binding} = sb.{attribute}\n"
        f"    {alias_chain}service.{method}('.')\n"
    )
    # When requesting guidance.
    result = suggest(source)
    # Then the suggested code uses the owning sandbox, not a nested service.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == ["sb." + form]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "service",
    [
        ("FileSystem", "list_files", "service.list(path, depth=depth)"),
        ("Git", "status", "service.status(path)"),
        (
            "Process",
            "exec",
            "service.run(command, cwd=cwd, env=env, execution_timeout=timeout)",
        ),
    ],
)
@pytest.mark.parametrize("alias", [False, True])
def test_service_level_form_when_parameter_owner_is_unknown(
    service: tuple[str, str, str], alias: bool
) -> None:
    # Given a service parameter with no provable sandbox owner.
    annotation, method, form = service
    parameter = "original" if alias else "service"
    assignment = "    service = original\n" if alias else ""
    source = (
        f"from daytona import {annotation}\n"
        f"def run({parameter}: {annotation}):\n"
        f"{assignment}    service.{method}('.')\n"
    )
    # When requesting guidance.
    result = suggest(source)
    # Then the replacement stays on the service handle itself.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == [form]


@pytest.mark.parametrize(
    "intervening",
    [
        "sb = other",
        "if flag:\n        sb = other",
        "if flag:\n        service = other.fs",
    ],
)
def test_service_level_form_when_owner_binding_becomes_ambiguous(
    intervening: str,
) -> None:
    # Given a saved service whose original owner name may now identify another sandbox.
    source = (
        "from daytona import Sandbox\n"
        "def run(sb: Sandbox, other: Sandbox, flag: bool):\n"
        "    service = sb.fs\n"
        f"    {intervening}\n"
        "    service.list_files('.')\n"
    )
    # When requesting guidance.
    result = suggest(source)
    # Then the tool does not attribute the saved service to the wrong sandbox.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == ["service.list(path, depth=depth)"]


def test_owner_when_aliases_are_shadowed_in_another_function() -> None:
    # Given identically named aliases belonging to distinct lexical scopes.
    source = (
        "from daytona import Sandbox, FileSystem\n"
        "def first(sb: Sandbox):\n"
        "    service = sb.fs\n"
        "    service.list_files('.')\n"
        "def second(service: FileSystem):\n"
        "    service.list_files('.')\n"
    )
    # When requesting guidance for both functions.
    result = suggest(source)
    # Then ownership does not leak into the unrelated service parameter.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == [
        "sb.fs.list(path, depth=depth)",
        "service.list(path, depth=depth)",
    ]


@pytest.mark.parametrize(
    "binding",
    [
        "service: FileSystem = sb.fs",
        "(service := sb.fs)",
        "first = service = sb.fs",
        "first = sb.fs\n    service = first",
        "holder.service = sb.fs\n    service = holder.service",
    ],
)
def test_owner_when_service_binding_uses_supported_assignment_syntax(
    binding: str,
) -> None:
    # Given different spellings of the same service binding.
    source = (
        "from daytona import Sandbox, FileSystem\n"
        "def run(sb: Sandbox, holder):\n"
        f"    {binding}\n"
        "    service.list_files('.')\n"
    )
    # When requesting guidance.
    result = suggest(source)
    # Then each binding carries the same sandbox ownership.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == ["sb.fs.list(path, depth=depth)"]


def test_owner_when_a_nested_service_is_aliased_through_a_parent_alias() -> None:
    # Given a nested receiver whose immediate parent is itself an alias.
    source = (
        "from daytona import Sandbox\n"
        "def run(sb: Sandbox):\n"
        "    computer = sb.computer_use\n"
        "    mouse = computer.mouse\n"
        "    mouse.get_position()\n"
    )
    # When requesting guidance.
    result = suggest(source)
    # Then the original sandbox is retained across both receiver levels.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == ["sb.computer_use.mouse.position()"]


def test_owner_when_unknown_service_is_a_dotted_parameter_attribute() -> None:
    # Given a service attribute whose containing object is not a sandbox.
    source = (
        "from daytona import FileSystem\n"
        "def run(holder):\n"
        "    holder.files: FileSystem\n"
        "    holder.files.list_files('.')\n"
    )
    # When requesting guidance.
    result = suggest(source)
    # Then no dotted segment is mistaken for the sandbox owner.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == ["holder.files.list(path, depth=depth)"]
