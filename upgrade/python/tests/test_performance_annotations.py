# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Forward-reference slicing uses the same indexed source boundaries as flow."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.annotations import rewrite_type_text
from daytona_upgrade.models import load_mapping
from daytona_upgrade.resolution import ProjectIndex, Resolver


def test_nested_annotations_migrate_when_whole_source_slicing_is_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given repeated nested forward references and an unavailable quadratic lookup.
    value = "tuple[" + ", ".join(["'daytona.Sandbox'"] * 64) + "]"
    resolver = Resolver(load_mapping(), ProjectIndex({}))

    def whole_source_slice(source: str, node: ast.AST) -> str | None:
        raise AssertionError("annotation lookup rescanned the complete source")

    monkeypatch.setattr(ast, "get_source_segment", whole_source_slice)
    # When the annotation is migrated.
    result = rewrite_type_text(value, resolver, {"daytona": "module:daytona"})
    # Then every nested reference is rewritten with its literal quotes preserved.
    assert result == "tuple[" + ", ".join(["'daytona.v0.Sandbox'"] * 64) + "]"
