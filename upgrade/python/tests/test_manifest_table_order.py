# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Disconnected TOML tables remain editable views of their original document."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "scenario",
    [
        (
            '[project]\ndependencies=["daytona>=0.210.0"]\n',
            "[project.optional-dependencies]\ntest=[]\n",
            "daytona>=0.210.0",
            "daytona[v0-sdk]>=2,<3",
        ),
        (
            '[project]\nname="x"\n',
            '[project.optional-dependencies]\ndaytona = ["daytona>=0.210.0"] # keep\n',
            "daytona>=0.210.0",
            "daytona[v0-sdk]>=2,<3",
        ),
        (
            '[dependency-groups]\na=["daytona>=0.210.0"]\n',
            "[dependency-groups.test]\nunused=1\n",
            "daytona>=0.210.0",
            "daytona[v0-sdk]>=2,<3",
        ),
        (
            '[tool.poetry]\nname="x"\n',
            '[tool.poetry.dependencies]\ndaytona="0.210.0" # keep\n',
            'daytona="0.210.0"',
            'daytona={version = ">=2,<3", extras = ["v0-sdk"]}',
        ),
        (
            "[tool.poetry.group.dev]\noptional=true\n",
            '[tool.poetry.group.dev.dependencies]\ndaytona="0.210.0"\n',
            'daytona="0.210.0"',
            'daytona={version = ">=2,<3", extras = ["v0-sdk"]}',
        ),
        (
            '[tool.poetry.dependencies]\npython="^3.10"\n',
            '[tool.poetry.dependencies.daytona]\nversion="0.210.0"\n',
            'version="0.210.0"',
            'version=">=2,<3"\nextras = ["v0-sdk"]',
        ),
        (
            '[tool.poetry.dev-dependencies]\nother="1"\n',
            '[tool.poetry.dev-dependencies.daytona]\nversion="0.210.0"\n',
            'version="0.210.0"',
            'version=">=2,<3"\nextras = ["v0-sdk"]',
        ),
        (
            '[tool.pdm]\nname="x"\n',
            '[tool.pdm.dev-dependencies]\ntest=["daytona>=0.210.0"]\n',
            "daytona>=0.210.0",
            "daytona[v0-sdk]>=2,<3",
        ),
        (
            '[tool.pdm.resolution.overrides]\ndaytona="0.210.0"\n',
            "[tool.pdm.resolution.overrides.other]\nx=1\n",
            'daytona="0.210.0"',
            'daytona=">=2,<3"',
        ),
        (
            '[tool.uv]\ndev-dependencies=["daytona>=0.210.0"]\n',
            "[tool.uv.pip]\nx=1\n",
            "daytona>=0.210.0",
            "daytona[v0-sdk]>=2,<3",
        ),
        (
            '[tool.uv]\noverride-dependencies=["daytona>=0.210.0"]\n',
            "[tool.uv.pip]\nx=1\n",
            "daytona>=0.210.0",
            "daytona[v0-sdk]>=2,<3",
        ),
        (
            '[tool.uv]\nconstraint-dependencies=["daytona"]\n',
            "[tool.uv.pip]\nx=1\n",
            '"daytona"',
            '"daytona>=2,<3"',
        ),
    ],
)
def test_migrates_dependencies_when_tables_are_disconnected(
    scenario: tuple[str, str, str, str],
) -> None:
    # Given a valid out-of-order parent table with unrelated intervening content.
    prefix, tail, before, after = scenario
    source = prefix + "\n[unrelated]\nx=1 # untouched\n\n" + tail
    # When editing the table through tomlkit's proxy.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then only the dependency changes; table order, quoting and comments survive.
    assert result.source == source.replace(before, after)
    assert not result.manual


def test_preserves_manifest_when_out_of_order_uv_source_overrides_dependency() -> None:
    # Given an SDK override in a disconnected uv table.
    source = '[project]\ndependencies=["daytona>=0.210.0"]\n[tool.uv]\nx=1\n[other]\nx=1\n[tool.uv.sources]\ndaytona={path="../sdk"}\n'
    # When source policy is inspected before registry dependency migration.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the override blocks automatic edits and retains review evidence.
    assert result.source == source
    assert result.found and result.manual and not result.changes


def test_edits_in_place_when_poetry_dependency_value_is_itself_a_proxy() -> None:
    # Given a disconnected dependency table, not merely its parent mapping.
    source = '[tool.poetry.dependencies.daytona]\nversion="0.210.0" # keep\n\n[other]\nx=1\n\n[tool.poetry.dependencies.daytona.custom]\nx=1\n'
    # When the dependency declaration itself is a live table proxy.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the original tables stay in place and retain their trivia.
    assert result.source == source.replace(
        'version="0.210.0" # keep', 'version=">=2,<3" # keep\nextras = ["v0-sdk"]'
    )
    assert result.changes and not result.manual
