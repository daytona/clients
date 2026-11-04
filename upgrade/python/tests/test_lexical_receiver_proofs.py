# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lexical receiver proofs remain conservative across conflicting bindings."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body",
    [
        "from other import *\nclient.create()",
        "try:\n    from other import *\nfinally:\n    client.create()",
    ],
)
def test_client_is_unproven_when_wildcard_can_replace_binding(body: str) -> None:
    # Given a proven client followed by an unknown wildcard import.
    source = "from daytona import Daytona\nclient = Daytona()\n" + body + "\n"
    # When resolving calls after the import, including exceptional prefixes.
    result = migrate(source)
    # Then an unrelated imported object cannot inherit the old client proof.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_parameter_moves_when_type_only_alias_conflicts_with_runtime() -> None:
    # Given an annotation-only SDK alias and a different runtime constructor.
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from daytona import Daytona as D\nelse:\n    from legacy import Other as D\ndef run(client: D):\n    client.create()\nD().create()\n"
    # When annotations and executable constructors are resolved separately.
    result = migrate(source)
    # Then only the annotated parameter receives the namespace insertion.
    assert result.source == source.replace("client.create()", "client.v0.create()")


def test_client_is_unproven_when_type_alias_belongs_to_class_scope() -> None:
    # Given a class-local type-only alias absent from the module namespace.
    source = "from typing import TYPE_CHECKING\nimport daytona\nclass C:\n    if TYPE_CHECKING:\n        from daytona import Daytona as D\nclient = Other()\nclient: D\nclient.create()\n"
    # When leaving the class and interpreting a later module annotation.
    result = migrate(source)
    # Then the class-local alias cannot establish a module proof.
    assert "client.v0.create()" not in result.source
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "nested",
    [
        "def helper():\n        client = Other()",
        "async def helper():\n        client = Other()",
        "class Helper:\n        client = Other()",
        "helper = lambda: (client := Other())",
        "helpers = [client for client in others]",
    ],
)
def test_enclosing_client_moves_when_nested_scope_reuses_name(nested: str) -> None:
    # Given a stable inherited client and an unrelated nested local binding.
    source = (
        "from daytona import Daytona\nclient = Daytona()\ndef run():\n    "
        + nested
        + "\n    client.create()\n"
    )
    # When collecting the function's actual lexical locals.
    result = migrate(source)
    # Then the helper's local does not hide the enclosing client.
    assert result.source == source.replace("client.create()", "client.v0.create()")


def test_attribute_is_unproven_when_type_alias_belongs_to_another_method() -> None:
    # Given an alias local to one method and an unresolved annotation in another.
    source = "from typing import TYPE_CHECKING\nimport daytona\nclass C:\n    def first(self):\n        if TYPE_CHECKING:\n            D = daytona.Daytona\n    def second(self):\n        self.client: D\n    def run(self):\n        self.client.create()\n"
    # When joining instance attribute declarations from distinct method scopes.
    result = migrate(source)
    # Then no method-local typing name becomes a class-wide client proof.
    assert "self.client.v0.create()" not in result.source
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "write",
    [
        "Alias = C\nAlias.client = Other()",
        "Alias = C\nAgain = Alias\nAgain.client = Other()",
        "Alias = C\ndel Alias.client",
    ],
)
def test_class_client_is_unproven_when_alias_mutates_attribute(write: str) -> None:
    # Given a concrete client default overwritten through a class alias.
    source = (
        "from daytona import Daytona\nclass C:\n    client = Daytona()\n"
        + write
        + "\nC.client.create()\n"
    )
    # When checking class-wide writes before using the default as a proof.
    result = migrate(source)
    # Then no stale class proof redirects the replacement receiver.
    assert "C.client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("declaration", ["client: Daytona", "client = Daytona()"])
def test_class_attribute_exists_only_when_declaration_has_value(
    declaration: str,
) -> None:
    # Given either a bare annotation or an actual class-body assignment.
    source = (
        "from daytona import Daytona\nclass C:\n    "
        + declaration
        + "\nC.client.create()\n"
    )
    # When determining whether C.client exists as a runtime value.
    result = migrate(source)
    # Then only the concrete class attribute is proven; the other stays manual.
    assert ("C.client.v0.create()" in result.source) == ("=" in declaration)
    assert any(item["kind"] == "fixme" for item in result.items) == (
        "=" not in declaration
    )
