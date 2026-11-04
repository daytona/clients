# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Wrapped writes and opaque construction cannot acquire rewrite proofs."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "write",
    [
        "client.create, other = values",
        "[client.create, other] = values",
        "(client.create, [other, *client.stop]) = values",
        "*(client.create), other = values",
        "(client.create) = stub",
        "del (client.create, client.stop)",
        "del [client.create, client.stop]",
        "del ((client.create), [client.stop])",
    ],
)
def test_methods_stay_manual_when_mutation_targets_are_wrapped(write: str) -> None:
    # Given mutation targets nested in legal Python target wrappers.
    source = "from daytona import Daytona\nclient = Daytona()\n" + write + "\n"
    # When migrating the writes or deletes.
    result = migrate(source)
    # Then mutation stays on the original object with review diagnostics.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert result.items
    assert all(item["kind"] == "mock-target" for item in result.items)
    assert "FIXME(daytona-upgrade)" in result.source


@pytest.mark.parametrize(
    "write",
    [
        "client.default_language, other = values",
        "[client.default_language] = values",
        "del (client.default_language,)",
    ],
)
def test_setting_moves_when_mutation_target_is_wrapped(write: str) -> None:
    # Given moved data rather than a replaced method.
    source = "from daytona import Daytona\nclient = Daytona()\n" + write + "\n"
    # When migrating the setting's wrapped write.
    result = migrate(source)
    # Then the established automatic data-write policy is unchanged.
    assert result.source == source.replace(
        "client.default_language", "client.v0.default_language"
    )
    assert result.items == ()


@pytest.mark.parametrize("module", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize("member", ["__file__", "__version__", "__custom__"])
@pytest.mark.parametrize(
    "layout",
    [
        "from {module} import {member} as metadata\n",
        "from {module} import Daytona, {member}\n",
        "from {module} import {member}; value = {member}\n",
    ],
)
def test_dunder_import_stays_on_root_when_package_is_renamed(
    module: str, member: str, layout: str
) -> None:
    # Given root module metadata imported as a name, alone or with ordinary exports.
    source = layout.format(module=module, member=member)
    # When migrating root imports.
    result = migrate(source)
    # Then metadata stays on the root, including unknown dunder names.
    assert result.source == source.replace("daytona_sdk", "daytona")
    assert result.items == ()


@pytest.mark.parametrize(
    "declarations",
    [
        "from external import Base\nclass Custom(Daytona, Base): pass",
        "from external import Base\nclass Custom(Base, Daytona): pass",
        "from external import Base\nclass Parent(Daytona, Base): pass\nclass Custom(Parent): pass",
        "class Custom(Daytona, **kw): pass",
        "class Parent(Daytona, **kw): pass\nclass Custom(Parent): pass",
    ],
)
def test_call_requires_review_when_construction_has_opaque_layers(
    declarations: str,
) -> None:
    # Given an unknown base or a class keyword expansion that can supply metaclass.
    source = "from daytona import Daytona\n" + declarations + "\nCustom().create()\n"
    # When migrating the constructed receiver.
    result = migrate(source)
    # Then uncertain construction cannot produce an automatic rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
