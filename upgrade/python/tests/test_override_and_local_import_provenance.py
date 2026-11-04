# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Binding-preserving decorators and lazy imports retain client field proofs."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    ("import_", "decorator"),
    [
        ("from typing import override", "override"),
        ("from typing_extensions import override as identity", "identity"),
        ("import typing as t", "t.override"),
        ("import typing_extensions", "typing_extensions.override"),
    ],
)
@pytest.mark.parametrize("receiver", ["self.client", "self.get_client()"])
def test_routes_client_when_method_has_proven_override_decorator(
    import_: str, decorator: str, receiver: str
) -> None:
    # Given both a field and a typed getter behind a binding-proven identity decorator.
    source = (
        f"from daytona import Daytona\n{import_}\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\n"
        f"    @{decorator}\n"
        "    def get_client(self) -> Daytona: return self.client\n"
        f"    @{decorator}\n"
        f"    def run(self): return {receiver}.create()\n"
    )
    # When migrating the class.
    result = migrate(source)
    # Then no real root-client access is silently left behind.
    assert f"{receiver}.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize(
    ("import_", "constructor"),
    [
        ("from daytona import Daytona", "Daytona"),
        ("from daytona_sdk import Daytona as Client", "Client"),
        ("import daytona as sdk", "sdk.Daytona"),
    ],
)
def test_routes_field_when_constructor_import_is_function_local(
    import_: str, constructor: str
) -> None:
    # Given a lazy optional dependency import, not a module-level binding.
    source = (
        f"class Service:\n    def __init__(self):\n        {import_}\n"
        f"        self.client = {constructor}()\n        self.client.create()\n"
        "    def run(self): return self.client.create()\n"
    )
    # When collecting writes across methods.
    result = migrate(source)
    # Then both immediate and deferred uses route to the same client namespace.
    assert result.source.count("self.client.v0.create()") == 2
    assert not result.items


@pytest.mark.parametrize(
    "replacement", ["Daytona = Other", "from other import Daytona"]
)
def test_preserves_field_when_local_sdk_import_is_rebound(replacement: str) -> None:
    # Given a local constructor name overwritten before assignment.
    source = (
        "class Service:\n    def __init__(self):\n"
        "        from daytona import Daytona\n"
        f"        {replacement}\n        self.client = Daytona()\n"
        "    def run(self): return self.client.create()\n"
    )
    # When collecting field provenance.
    result = migrate(source)
    # Then the earlier import cannot certify the replacement.
    assert "self.client.v0" not in result.source


def test_preserves_override_when_project_return_annotation_is_a_base_class() -> None:
    # Given a concrete returned subclass which contradicts the declared method contract.
    source = (
        "from daytona import Daytona\nclass Manager:\n"
        "    def get_client(self) -> Daytona: return Daytona()\n"
        "class Child(Manager):\n    def get_client(self): return Other()\n"
        "def singleton() -> Manager: return Child()\n"
        "client = singleton().get_client()\nclient.create()\n"
    )
    # When inferring the singleton's method results.
    result = migrate(source)
    # Then the upper-bound annotation does not authorize calling the SDK instead.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


def test_preserves_newlines_when_poetry_table_needs_extras() -> None:
    # Given a CRLF manifest without an extras array.
    source = '[tool.poetry.dependencies.daytona]\r\nversion = "^0.224.0"\r\n'
    # When adding the compatibility extra.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then every inserted record uses the source newline convention.
    assert 'extras = ["v0-sdk"]\r\n' in result.source
    assert "\n" not in result.source.replace("\r\n", "")
