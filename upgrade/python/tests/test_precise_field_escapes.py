# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Precise reflective destinations do not exempt escaping replacement values."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "write",
    [
        "setattr(other, 'sibling', holder)",
        "object.__setattr__(other, 'sibling', holder)",
        "vars(other)['sibling'] = holder",
        "vars(other).update(sibling=holder)",
        "other.__dict__['sibling'] = holder",
        "other.__dict__.update(sibling=holder)",
    ],
)
def test_replacement_requires_review_when_stored_by_precise_write(write: str) -> None:
    # Given independent holders with proven fields and a literal sibling store.
    source = (
        "from daytona import Daytona\ndef run(holder, other):\n"
        "    holder.client = Daytona()\n    other.client = Daytona()\n"
        f"    {write}\n    holder.client.create()\n    other.client.create()\n"
    )
    # When a precise write stores one holder inside the other.
    result = migrate(source)
    # Then only the escaping replacement loses its field proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(
        ast.parse(source.replace("other.client.create()", "other.client.v0.create()"))
    )
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "operation",
    [
        "vars(holder)",
        "opaque(vars(holder))",
        "vars(holder)['sibling']",
        "vars(holder).update(**values)",
        "vars(holder).setdefault('sibling', value)",
        "setattr(holder, key, value)",
        "object.__setattr__(holder, key, value)",
        "setattr(holder, 'sibling', opaque(holder))",
        "vars(holder).update(sibling=opaque(holder))",
        "vars(holder)['sibling'] = opaque(holder)",
        "holder()",
    ],
)
def test_fields_require_review_when_call_is_not_only_a_precise_store(
    operation: str,
) -> None:
    # Given an ordinary call or an opaque nested call inside a literal store.
    source = (
        "from daytona import Daytona\ndef run(holder, key, value, values):\n"
        "    holder.client = Daytona()\n"
        f"    {operation}\n    holder.client.create()\n"
    )
    # When the holder crosses an opaque call boundary.
    result = migrate(source)
    # Then no reflective-reader whitelist or nested-store exemption proves it safe.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("operation", ["opaque(Holder)", "alias = Holder"])
def test_class_default_requires_review_when_class_object_escapes(
    operation: str,
) -> None:
    # Given a known class with a direct client default, not a factory.
    source = (
        "from daytona import Daytona\nclass Holder:\n    client = Daytona()\n"
        f"{operation}\nHolder().client.create()\n"
    )
    # When the class itself is passed or stored rather than merely constructed.
    result = migrate(source)
    # Then its default field cannot be automatically redirected.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
