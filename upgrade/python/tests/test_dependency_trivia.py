# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency replacements retain TOML value trivia across supported layouts."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from tomlkit.items import String

from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping
from daytona_upgrade.toml_values import toml_string


@pytest.mark.parametrize(
    "source",
    [
        '[project]\ndependencies = [\n  "daytona>=0.210.0",  # pinned for x\n]\n',
        '[project.optional-dependencies]\ntest = [\n  "daytona>=0.210.0",  # pinned for x\n]\n',
        '[tool.pdm.dev-dependencies]\ntest = [\n  "daytona>=0.210.0",  # pinned for x\n]\n',
        '[tool.poetry.dependencies]\ndaytona = { version = "0.210.0", extras = [] } # pinned for x\n',
        '[tool.poetry.dependencies.daytona]\nversion = "0.210.0"  # pinned for x\n',
        '[tool.pdm.resolution.overrides]\ndaytona = "0.210.0"  # pinned for x\n',
    ],
)
def test_keeps_comment_when_dependency_value_is_replaced(source: str) -> None:
    # Given a dependency with inline comment, indentation and original spacing.
    # When migrating the parsed manifest through its normal dependency readers.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the dependency changes without losing its adjacent comment or spacing.
    expected = source.replace("daytona>=0.210.0", "daytona[v0-sdk]>=2,<3").replace(
        '"0.210.0"', '">=2,<3"'
    )
    if "extras = []" in source:
        expected = expected.replace("extras = []", 'extras = ["v0-sdk"]')
    if "[tool.poetry.dependencies.daytona]" in source:
        expected += 'extras = ["v0-sdk"]\n'
    assert result.source == expected
    assert result.changes and not result.manual


def test_keeps_value_trivia_when_string_is_rebuilt() -> None:
    # Given trivia attached to the value itself, not an array's comma token.
    original = tomlkit.parse('version = "0.210.0"  # pinned for x\n')["version"]
    assert isinstance(original, String)
    original.trivia.indent = "  "
    original.trivia.trail = " \n"
    # When constructing a replacement before its table/array inserts it.
    replacement = toml_string(original, ">=2,<3")
    # Then all surrounding trivia survives independently of container behavior.
    for field in ("indent", "comment_ws", "comment", "trail"):
        assert getattr(replacement.trivia, field) == getattr(original.trivia, field)
