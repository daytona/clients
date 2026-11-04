# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Uncertain writes and callable evaluation must not bypass replacements."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "write",
    [
        "setattr(receiver, name, stub)",
        "receiver.__dict__.update(mapping)",
        "vars(receiver).update(**kw)",
    ],
)
@pytest.mark.parametrize("member", ["create", "stop", "snapshot.list"])
def test_dispatch_requires_review_when_instance_write_has_unknown_member(
    write: str, member: str
) -> None:
    # Given a wildcard write recorded under the instance's normalized root.
    source = (
        "from daytona import Daytona\nclass Custom(Daytona):\n"
        "    def install(receiver):\n"
        f"        {write}\nCustom().{member}()\n"
    )
    # When migrating a moved member potentially replaced by that write.
    result = migrate(source)
    # Then no moved dispatch can bypass the replacement.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "write",
    [
        "setattr(Daytona, name, stub)",
        "Daytona.__dict__.update(mapping)",
        "vars(Daytona).update(**kw)",
    ],
)
@pytest.mark.parametrize("member", ["create", "stop", "snapshot.list"])
def test_dispatch_requires_review_when_class_write_has_unknown_member(
    write: str, member: str
) -> None:
    # Given a wildcard class mutation that can replace inherited SDK members.
    source = (
        f"from daytona import Daytona\nclient = Daytona()\n{write}\nclient.{member}()\n"
    )
    # When migrating a later instance call.
    result = migrate(source)
    # Then class mutations fence every possibly replaced moved member.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "decorator",
    [
        "property",
        "functools.cached_property",
        "value.setter",
        "unknown",
        "unknown()",
        "staticmethod",
        "classmethod",
    ],
)
@pytest.mark.parametrize("member", ["create", "extra"])
def test_dispatch_when_decorated_method_may_replace_instance_or_class_member(
    decorator: str, member: str
) -> None:
    # Given a decorated method whose first argument may be the instance or class.
    source = (
        "from daytona import Daytona\nimport functools\nclass Custom(Daytona):\n"
        f"    @{decorator}\n    def install(cls):\n"
        f"        cls.{member} = stub\nCustom().create()\n"
    )
    # When analyzing possible dispatch after that method executes.
    result = migrate(source)
    # Then moved writes fence dispatch without losing unrelated-member precision.
    expected = (
        source
        if member == "create"
        else source.replace("().create()", "().v0.create()")
    )
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))
    assert any(item["kind"] == "fixme" for item in result.items) is (member == "create")


@pytest.mark.parametrize("callee", ["install", "alias"])
def test_helper_effect_survives_when_argument_rebinds_callee(callee: str) -> None:
    # Given a callable fetched before an argument replaces its binding.
    source = (
        "from daytona import Daytona\ndef install(client: Daytona):\n"
        "    client.create = stub\ndef harmless(client): pass\nalias = install\n"
        f"client = Daytona()\n{callee}(({callee} := harmless, client)[1])\n"
        "client.create()\n"
    )
    # When applying the original callable's effects after evaluating arguments.
    result = migrate(source)
    # Then rebinding cannot erase the replacement the fetched callable performs.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


def test_holder_field_requires_review_when_local_helper_receives_self() -> None:
    # Given a project-local helper rather than an external function.
    source = (
        "from daytona import Daytona\ndef inspect_holder(holder): pass\n"
        "class Service:\n    def __init__(self): self.client = Daytona()\n"
        "    def run(self):\n        inspect_holder(self)\n        self.client.create()\n"
    )
    # When self is passed as an argument, not used as the method receiver.
    result = migrate(source)
    # Then the documented conservative escape rule applies even to local helpers.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
