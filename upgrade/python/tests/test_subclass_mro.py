# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only definitions preceding the SDK in C3 order own client members."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "bases,rewrite", [("Daytona, Mixin", True), ("Mixin, Daytona", False)]
)
@pytest.mark.parametrize("use", ["Custom().create()", "getattr(Custom(), 'create')()"])
def test_member_ownership_follows_mro_when_mixin_defines_same_member(
    bases: str, rewrite: bool, use: str
) -> None:
    # Given identical project members on opposite sides of the SDK in the MRO.
    source = (
        "from daytona import Daytona\nclass Mixin:\n    def create(self): pass\n"
        f"class Custom({bases}): pass\n{use}\n"
    )
    # When migrating an ordinary call or builtin lookup.
    result = migrate(source)
    # Then only the SDK-owned lookup moves to v0.
    assert result.source == (
        source.replace("Custom()", "Custom().v0") if rewrite else source
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "declarations,rewrite",
    [
        (
            "class Left(Daytona): pass\nclass Right(Daytona):\n    def create(self): pass\nclass Custom(Left, Right): pass",
            False,
        ),
        (
            "class Mixin:\n    def create(self): pass\nclass Left(Daytona, Mixin): pass\nclass Right(Mixin): pass\nclass Custom(Left, Right): pass",
            True,
        ),
    ],
)
def test_member_ownership_uses_c3_when_project_bases_form_diamond(
    declarations: str, rewrite: bool
) -> None:
    # Given a diamond where depth-first search or a base-member union is wrong.
    source = f"from daytona import Daytona\n{declarations}\nCustom().create()\n"
    # When resolving the project-known hierarchy.
    result = migrate(source)
    # Then the actual C3 winner determines the migration.
    assert result.source == (
        source.replace("Custom().create", "Custom().v0.create") if rewrite else source
    )
    assert result.items == ()


@pytest.mark.parametrize(
    # Even a trailing opaque base precedes object and can replace construction
    # through __new__ or its metaclass; member lookup alone cannot prove it safe.
    "bases,manual",
    [("Unknown, Daytona", True), ("Daytona, Unknown", True)],
)
def test_unknown_base_is_manual_when_it_precedes_sdk(bases: str, manual: bool) -> None:
    # Given an external base whose methods the project cannot inspect.
    source = f"from external import Unknown\nfrom daytona import Daytona\nclass Custom({bases}): pass\nCustom().create()\n"
    # When migrating the inherited-looking member.
    result = migrate(source)
    # Then uncertainty before the SDK produces a FIXME, not a guessed rewrite.
    if manual:
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert [item["kind"] for item in result.items] == ["fixme"]
    else:
        assert result.source == source.replace("Custom().create", "Custom().v0.create")
        assert result.items == ()


def test_member_ownership_survives_when_hierarchy_is_imported() -> None:
    # Given a constructor re-export whose trailing mixin defines create.
    sources = {
        "app": "from base import Custom\nCustom().create()\n",
        "base": "from daytona import Daytona\nclass Mixin:\n    def create(self): pass\nclass Custom(Daytona, Mixin): pass\n",
    }
    mapping = load_mapping()
    # When the consumer receives its proof through fixed-point indexing.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then MRO provenance is not flattened away at the module boundary.
    assert result.source == sources["app"].replace(
        "Custom().create", "Custom().v0.create"
    )
    assert result.items == ()


def test_unknown_base_stays_first_when_distinct_external_bases_are_merged() -> None:
    # Given independent external bases which must not collapse to one C3 identity.
    source = (
        "from external import Unknown, Other\nfrom daytona import Daytona\n"
        "class First(Unknown, Daytona): pass\nclass Second(Other, Daytona):\n"
        "    def create(self): pass\nclass Custom(First, Second): pass\nCustom().create()\n"
    )
    # When C3 merges the two incomplete project-known hierarchies.
    result = migrate(source)
    # Then the first external base still prevents proven project ownership.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
