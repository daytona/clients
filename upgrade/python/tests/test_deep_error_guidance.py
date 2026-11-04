# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared-error suggestions include supported v0 submodule imports."""

from __future__ import annotations

import ast

import pytest
from test_shared_errors import transform


@pytest.mark.parametrize("root", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize("name", ["DaytonaError", "DaytonaError as Failure", "*"])
def test_suggests_when_shared_errors_are_imported_from_a_v0_submodule(
    root: str, name: str
) -> None:
    # Given a supported deep v0 error import, including aliases and star imports.
    source = f"from {root}.v0.common.errors import {name}\n"
    # When requesting v2 guidance.
    result = transform(source, "suggest-v2")
    # Then the import receives a suggestion without changing its runtime identity.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("suggestion", 1)
    ]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "from daytona.v0.common.errors import Sandbox\n",
        "from daytona.v01.common.errors import DaytonaError\n",
        "from .daytona.v0.common.errors import DaytonaError\n",
    ],
)
def test_preserves_when_deep_import_is_not_a_supported_shared_error(
    source: str,
) -> None:
    # Given a resource, a lookalike module prefix, or a project-relative import.
    # When requesting v2 guidance.
    result = transform(source, "suggest-v2")
    # Then prefix matching does not invent error suggestions.
    assert result.source == source
    assert not result.items


@pytest.mark.parametrize("root", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize("aliased", [False, True])
def test_suggests_when_deep_module_import_proves_a_shared_error_use(
    root: str, aliased: bool
) -> None:
    # Given a deep namespace import with an evaluated shared-error attribute.
    module = f"{root}.v0.common.errors"
    binding = "errors" if aliased else module
    source = f"import {module}" + (" as errors" if aliased else "")
    source += f"\nerror_type = {binding}.DaytonaError\n"
    # When requesting v2 guidance.
    result = transform(source, "suggest-v2")
    # Then the suggestion attaches to the import without rewriting the use.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("suggestion", 1)
    ]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
