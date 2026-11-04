# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Service methods preserve class-joined proofs without exempting external escapes."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "operation", ["", "params = self._params()", "helper(self)", "str(self)"]
)
@pytest.mark.parametrize("declaration", ["instance", "class", "inferred"])
def test_service_field_when_method_or_external_call_precedes_use(
    operation: str, declaration: str
) -> None:
    # Given a service holder, with paired annotation/inference cases.
    source = (
        "from daytona import Daytona\nfrom external import helper\nclass Service:\n"
        + ("    daytona: Daytona\n" if declaration == "class" else "")
        + "    def __init__(self):\n        self.daytona"
        + (": Daytona" if declaration == "instance" else "")
        + " = Daytona()\n    def _params(self): return {}\n"
        + "    def run(self):\n"
        + (f"        {operation}\n" if operation else "")
        + "        self.daytona.create()\n"
    )
    # When migrating a call after an internal method or an external escape.
    result = migrate(source)
    # Then only external calls receiving an unannotated self lose field proof.
    fenced = declaration == "inferred" and operation in {"helper(self)", "str(self)"}
    expected = (
        source if fenced else source.replace("daytona.create()", "daytona.v0.create()")
    )
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))
    assert [item["kind"] for item in result.items] == (["fixme"] if fenced else [])


@pytest.mark.parametrize("assignment", ["holder = holder", "holder: Holder = holder"])
def test_holder_field_when_assignment_preserves_binding(assignment: str) -> None:
    # Given a proven field before an identity-preserving assignment.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\n" + assignment + "\nholder.client.create()\n"
    )
    # When migrating the later field use.
    result = migrate(source)
    # Then storing the same binding is not an escape or a field rebind.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "expression",
    [
        "[holder for holder in range(1)]",
        "{holder for holder in range(1)}",
        "{holder: holder for holder in range(1)}",
        "(holder for holder in range(1))",
        "[holder.client for holder in values]",
        "[part for holder in values for part in holder.parts]",
    ],
)
def test_holder_field_when_comprehension_shadows_argument_root(expression: str) -> None:
    # Given an outer holder unrelated to the comprehension's local holder binding.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\nopaque("
        + expression
        + ")\nholder.client.create()\n"
    )
    # When passing the comprehension's result to an opaque function.
    result = migrate(source)
    # Then the outer holder's field proof survives.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "expression",
    [
        "[holder for item in range(1)]",
        "[item for item in holder]",
        "[item for item in values if holder]",
        "[part for item in values for part in holder]",
        "[holder for holder in holder]",
    ],
)
def test_holder_requires_review_when_comprehension_references_outer_root(
    expression: str,
) -> None:
    # Given a comprehension that still references the real outer holder.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\nopaque("
        + expression
        + ")\nholder.client.create()\n"
    )
    # When the opaque call can receive or observe that holder.
    result = migrate(source)
    # Then shadow filtering must not hide the outer escape.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("operation", ["self._params()", "helper(self)", "str(self)"])
def test_service_field_requires_review_when_class_method_replaces_client(
    operation: str,
) -> None:
    # Given a client annotation contradicted by a concrete in-class write.
    source = (
        "from daytona import Daytona\nclass Service:\n    daytona: Daytona\n"
        "    def __init__(self): self.daytona = Daytona()\n"
        "    def _params(self): self.daytona = Other()\n"
        f"    def run(self):\n        {operation}\n        self.daytona.create()\n"
    )
    # When migrating the use after a method or external call.
    result = migrate(source)
    # Then annotations do not overrule contradictory concrete writes.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_holder_field_moves_when_explicitly_annotated_before_external_escape() -> None:
    # Given an ordinary holder with an explicitly typed client field.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client: Daytona = Daytona()\nopaque(holder)\nholder.client.create()\n"
    )
    # When the holder escapes after its declaration.
    result = migrate(source)
    # Then an explicit field contract retains proof just like a typed parameter.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()
