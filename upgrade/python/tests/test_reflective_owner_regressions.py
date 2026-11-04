# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective aliases cannot preserve proofs on another possible destination."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "alias",
    [
        "o = self if flag else other",
        "o = other if flag else self",
        "if flag:\n    o = self\nelse:\n    o = other",
        "o = self\nif flag: o = other",
        "first = self if flag else other\no = first",
    ],
)
@pytest.mark.parametrize("write", ["setattr(o, 'c', h)", "vars(o)['c'] = h"])
def test_possible_owners_are_fenced_when_reflective_alias_is_ambiguous(
    alias: str, write: str
) -> None:
    # Given a class-wide client proof and an alias that may refer to its owner.
    body = "\n".join("        " + line for line in alias.splitlines())
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self): self.c = Daytona()\n"
        f"    def replace(self, other, h, flag):\n{body}\n        {write}\n"
        "    def run(self):\n        self.c.create()\n"
    )
    # When joining reflective stores across all methods.
    result = migrate(source)
    # Then no possible self write leaves an automatic rewrite on its consumer.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("fixme", len(source.splitlines()))
    ]


@pytest.mark.parametrize(
    "setup",
    ["v = vars", "v = vars\nv = unknown", "vars = unknown\nv = vars"],
)
@pytest.mark.parametrize("scope", ["local", "class", "loop"])
def test_all_fields_are_fenced_when_vars_alias_is_unproven(
    setup: str, scope: str
) -> None:
    # Given an opaque dictionary provider that need not honor vars' owner/key.
    body = (
        "    for _ in values:\n        self.backup.create()\n        v(self)['k'] = x\n"
        if scope == "loop"
        else "    v(self)['k'] = x\n    self.backup.create()\n"
    )
    source = "from daytona import Daytona\n" + setup + "\n"
    source += (
        "class Owner:\n    def __init__(self): self.backup = Daytona()\n"
        "    def replace(self, x): v(self)['k'] = x\n"
        "    def run(self): self.backup.create()\n"
        if scope == "class"
        else "def run(self, x, values):\n    self.backup = Daytona()\n" + body
    )
    # When migrating another field on the owner or the loop's next iteration.
    result = migrate(source)
    # Then only linked uncertainty survives the whole-owner fence.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "setup",
    [
        "fields = {'c': d}\nfields['c'] = unknown()",
        "fields = {'c': d}\nalias = fields\nalias.update(c=unknown())",
        "if flag:\n    fields = {'c': d}\nelse:\n    fields = unknown()",
        "if flag:\n    fields = {'c': d}\nelse:\n    fields = {'other': d}",
    ],
)
def test_dictionary_update_stays_unproven_when_literal_shape_is_lost(
    setup: str,
) -> None:
    # Given dictionary contents which may differ from their original literal.
    body = "\n".join("    " + line for line in setup.splitlines())
    source = (
        "from daytona import Daytona\ndef run(self, flag):\n    d = Daytona()\n"
        f"{body}\n    self.__dict__.update(fields)\n    self.c.create()\n"
    )
    # When considering the literal's earlier client value.
    result = migrate(source)
    # Then uncertain keys or mutations never certify an attribute rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_owner_link_stays_unproven_when_original_binding_is_replaced() -> None:
    # Given a definite alias whose original name now refers to another object.
    source = (
        "from daytona import Daytona\ndef run(self, other):\n"
        "    obj = self\n    self = other\n    vars(obj).update(c=Daytona())\n"
        "    self.c.create()\n"
    )
    # When the write still targets the original object through its alias.
    result = migrate(source)
    # Then the replacement object cannot gain a proven client attribute.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
