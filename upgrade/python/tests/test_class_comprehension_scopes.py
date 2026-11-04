# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class comprehension scopes and annotations must preserve runtime identity."""

from __future__ import annotations

from textwrap import indent as indent_source

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "expression",
    [
        "[client.create() for i in [1]]",
        "{client.create() for i in [1]}",
        "{i: client.create() for i in [1]}",
        "{client.create(): i for i in [1]}",
        "(client.create() for i in [1])",
        "[i for i in [1] if client.create()]",
        "[j for i in [1] for j in client.create()]",
        "[[client.create() for j in [1]] for i in [1]]",
        "[i for i in [j for j in client.create()]]",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("outer_client", [False, True])
@pytest.mark.parametrize("function_scope", [False, True])
def test_comprehension_uses_nonclass_scope_when_evaluating_body(
    expression: str, nested: bool, outer_client: bool, function_scope: bool
) -> None:
    # Given contradictory class and enclosing bindings, optionally nested classes.
    outer = "Daytona()" if outer_client else "Other()"
    local = "Other()" if outer_client else "Daytona()"
    indent = "        " if nested else "    "
    source = (
        f"from daytona import Daytona\nclient = {outer}\nclass Outer:\n"
        + (f"    client = {local}\n    class Inner:\n" if nested else "")
        + f"{indent}client = {local}\n{indent}values = {expression}\n"
    )
    if function_scope:
        source = "def run():\n" + indent_source(source, "    ")
    # When migrating accesses whose runtime lookup skips the class namespace.
    result = migrate(source)
    # Then only the first iterable (including nested comprehensions in it) sees locals.
    first_iterable = expression == "[i for i in [j for j in client.create()]]"
    should_route = not outer_client if first_iterable else outer_client
    assert ("client.v0.create()" in result.source) is should_route


@pytest.mark.parametrize("scope", ["module", "function", "class"])
@pytest.mark.parametrize("initial", ["Other()", "CustomDaytona()", "Daytona()"])
def test_annotation_preserves_binding_when_no_value_is_assigned(
    scope: str, initial: str
) -> None:
    # Given a known value whose runtime identity an annotation cannot replace.
    header = {"module": "", "function": "def run():\n", "class": "class Owner:\n"}
    indent = "" if scope == "module" else "    "
    source = (
        "from daytona import Daytona\nclass CustomDaytona(Daytona):\n"
        "    def create(self):\n        return 'custom'\n"
        + header[scope]
        + f"{indent}client = {initial}\n{indent}client: Daytona\n"
        + f"{indent}client.create()\n"
    )
    # When migrating the later access.
    result = migrate(source)
    # Then concrete SDK proof survives, but a contradictory annotation creates none.
    assert ("client.v0.create()" in result.source) is (initial == "Daytona()")


def test_annotation_keeps_client_proof_when_type_is_unrelated() -> None:
    # Given a client and a later annotation without runtime assignment.
    source = "from daytona import Daytona\nclient = Daytona()\nclient: Other\nclient.create()\n"
    # When upgrading the access.
    result = migrate(source)
    # Then the original client proof remains usable.
    assert "client.v0.create()" in result.source


def test_comprehension_keeps_mutation_fence_when_entering_nonclass_scope() -> None:
    # Given an eagerly evaluated first iterable replacing a global list's contents.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\nclass Owner:\n"
        "    values = [clients[0].create() for i in [clients.pop(), clients.append(Other())]]\n"
    )
    # When the element switches from class scope to its enclosing namespace.
    result = migrate(source)
    # Then switching scopes must not revive the mutated container's old proof.
    assert "clients[0].v0.create()" not in result.source


@pytest.mark.parametrize("outer", ["Other()", "Daytona()"])
def test_class_field_annotation_proves_client_when_outer_name_is_bound(
    outer: str,
) -> None:
    # Given an outer binding unrelated to a separately annotated instance field.
    source = (
        f"from daytona import Daytona\nclient = {outer}\nclass Owner:\n"
        "    client: Daytona\n    def run(self):\n        return self.client.create()\n"
    )
    # When collecting class-wide instance proofs.
    result = migrate(source)
    # Then the outer binding does not suppress the field annotation.
    assert result.source == source.replace(
        "self.client.create()", "self.client.v0.create()"
    )


@pytest.mark.parametrize(
    "expression",
    [
        "[self.client.create() for i in [1]]",
        "{self.client.create() for i in [1]}",
        "{i: self.client.create() for i in [1]}",
        "(self.client.create() for i in [1])",
        "lambda: self.client.create()",
    ],
)
@pytest.mark.parametrize("outer_client", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_class_expression_uses_outer_self_when_class_has_synthetic_receiver(
    expression: str, outer_client: bool, nested: bool
) -> None:
    # Given distinct outer self and class instance field identities.
    outer = "Daytona()" if outer_client else "Other()"
    local = "Other()" if outer_client else "Daytona()"
    source = (
        "from daytona import Daytona\nclass Other:\n    pass\n"
        f"self = Other()\nself.client = {outer}\nclass Owner:\n"
        + ("    class Inner:\n" if nested else "")
        + indent_source(
            f"client = {local}\nvalues = {expression}\n",
            "        " if nested else "    ",
        )
    )
    # When the comprehension or lambda resolves self outside the class namespace.
    result = migrate(source)
    # Then only the genuine enclosing receiver controls namespace routing.
    assert ("self.client.v0.create()" in result.source) is outer_client
