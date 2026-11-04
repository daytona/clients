# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded receiver proofs preserve literal target formatting."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("base", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize("receiver", ["super()", "super(Custom, self)"])
@pytest.mark.parametrize("member", ["create()", "snapshot.list()", "warm_pool"])
def test_super_requires_review_when_inside_client_subclass(
    base: str, receiver: str, member: str
) -> None:
    # Given an inherited access whose super receiver cannot safely become v0.
    source = f"from daytona import {base}\nclass Custom({base}):\n    def run(self):\n        return {receiver}.{member}\n"
    # When upgrading the class body.
    result = migrate(source)
    # Then code remains intact, but strict mode sees manual work, idempotently.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert migrate(result.source).source == result.source


@pytest.mark.parametrize(
    "setup",
    [
        "import httpx\napi_client = httpx.Client()",
        "import httpx as web\napi_client = web.AsyncClient()",
        "from httpx import Client as HTTP\napi_client = HTTP()",
        "from requests import Session\napi_client = Session()",
        "import vendor\napi_client = vendor.Client()",
        "class HTTP:\n    def get(self, url): pass\napi_client = HTTP()",
        "api_client = {'url': 'value'}",
    ],
)
@pytest.mark.parametrize(
    "access", ["api_client.get(url)", "getattr(api_client, 'get')"]
)
def test_known_other_client_stays_when_name_matches_heuristic(
    setup: str, access: str
) -> None:
    # Given non-Daytona objects with client-like names in a Daytona-using file.
    source = f"from daytona import Daytona\n{setup}\n{access}\n"
    # When inspecting moved-name accesses.
    result = migrate(source)
    # Then neither executable code nor manual diagnostics change.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "setup",
    [
        "api_client = unknown()",
        "import httpx\nhttpx = unknown()\napi_client = httpx.Client()",
        "from httpx import Client\nClient = unknown\napi_client = Client()",
    ],
)
def test_unknown_generic_client_stays_when_daytona_link_is_absent(setup: str) -> None:
    # Given unknown construction with no link to the unrelated Daytona import.
    source = f"from daytona import Daytona\n{setup}\napi_client.get(url)\n"
    # When applying the heuristic fallback.
    result = migrate(source)
    # Then generic client spelling cannot justify a manual item.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("quote", ["'", '"', "'''", '"""'])
@pytest.mark.parametrize("prefix", ["", "r", "u"])
@pytest.mark.parametrize("call", ["patch", "import_module"])
def test_target_keeps_delimiters_when_namespace_changes(
    quote: str, prefix: str, call: str
) -> None:
    # Given supported string targets using the author's chosen delimiters.
    target = "daytona_sdk.Sandbox" if call == "patch" else "daytona_sdk.sandbox"
    source = f"from unittest.mock import patch\nfrom importlib import import_module\n{call}({prefix}{quote}{target}{quote})\n"
    # When migrating the target namespace.
    result = migrate(source)
    # Then only its module path changes, including on subsequent runs.
    expected = "daytona.v0.Sandbox" if call == "patch" else "daytona.v0.sandbox"
    assert result.source == source.replace(target, expected)
    assert migrate(result.source).source == result.source


@pytest.mark.parametrize(
    "assignment",
    [
        "first, second = Daytona(), AsyncDaytona()",
        "[first, second] = [Daytona(), AsyncDaytona()]",
        "first, [second] = Daytona(), [AsyncDaytona()]",
        "first, second = Daytona(), other",
        "second, first = other, Daytona()",
        "first, second = second, first",
    ],
)
def test_unpack_retains_each_proof_when_literal_shape_matches(assignment: str) -> None:
    # Given literal destructuring, including a swap needing pre-store proofs.
    source = f"from daytona import Daytona, AsyncDaytona\nfirst = other\nsecond = Daytona()\n{assignment}\nfirst.create()\nsecond.create()\n"
    expected = source.replace("first.create()", "first.v0.create()")
    if "AsyncDaytona()" in assignment:
        expected = expected.replace("second.create()", "second.v0.create()")
    # When binding the individual literal elements.
    result = migrate(source)
    # Then only targets paired with client-valued elements are rewritten.
    assert result.source == expected
    assert result.items == ()


@pytest.mark.parametrize(
    "assignment",
    [
        "first, *second = Daytona(), Daytona()",
        "first, second = Daytona(), *unknown",
        "first, second = Daytona(), Daytona(), Daytona()",
        "first, second = unknown",
        "first, second = (Daytona() for _ in values)",
    ],
)
def test_unpack_loses_proof_when_shape_is_uncertain(assignment: str) -> None:
    # Given unknown, mismatched or starred unpacking of earlier client bindings.
    source = f"from daytona import Daytona\nfirst = second = Daytona()\n{assignment}\nfirst.create()\nsecond.create()\n"
    # When refusing to infer an unproven target shape.
    result = migrate(source)
    # Then prior proofs cannot leak into either receiver.
    assert result.source == source


def test_super_stays_when_builtin_is_shadowed() -> None:
    # Given a project-owned callable named super in an SDK subclass method.
    source = "from daytona import Daytona\nclass Custom(Daytona):\n    def run(self, super):\n        return super().create()\n"
    # When inspecting this non-builtin expression.
    result = migrate(source)
    # Then no built-in-super claim is made.
    assert result.source == source
    assert result.items == ()


def test_strict_fails_when_super_requires_manual_migration(tmp_path: Path) -> None:
    # Given a real project whose subclass invokes an inherited SDK method.
    path = tmp_path / "app.py"
    path.write_text(
        "from daytona import Daytona\nclass Custom(Daytona):\n    def run(self):\n        return super().create()\n"
    )
    # When executing the actual CLI's strict upgrade path.
    status = cli.main([str(tmp_path), "--force", "--yes", "--strict"])
    # Then the unchanged call is annotated and strict mode cannot report clean.
    assert status == 1
    assert "FIXME(daytona-upgrade)" in path.read_text()
    assert "return super().create()" in path.read_text()


@pytest.mark.parametrize(
    "import_line",
    [
        "import httpx\napi_client = httpx.Client()",
        "from httpx import Client\napi_client = Client()",
    ],
)
def test_generic_constructor_stays_when_project_module_has_no_daytona_link(
    import_line: str,
) -> None:
    # Given a local module with a callable whose return remains unresolved.
    source = f"from daytona import Daytona\n{import_line}\napi_client.get(url)\n"
    mapping = load_mapping()
    sources = {"app": source, "httpx": "def Client(): return unknown()\n"}
    # When analyzing with the actual project index rather than a name allowlist.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then project ownership alone does not link the unknown value to Daytona.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "literal",
    [
        '"daytona_sdk.Sandbox.quoted\\"name"',
        "r'daytona_sdk.Sandbox.quoted\\'name'",
        '"daytona_sdk.Sandbox.line\\nname"',
    ],
)
def test_target_value_survives_when_literal_contains_escapes(literal: str) -> None:
    # Given a literal target whose suffix contains escapes rather than identifiers.
    source = f"from unittest.mock import patch\npatch({literal})\n"
    expected = ast.parse(source.replace("daytona_sdk.Sandbox", "daytona.v0.Sandbox"))
    # When migrating its namespace while preserving the original delimiter style.
    result = migrate(source)
    # Then parsing observes exactly the intended new value, not different escapes.
    assert ast.dump(ast.parse(result.source)) == ast.dump(expected)
