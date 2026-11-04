# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression drivers for call-site bindings, collisions and include modes."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from tomlkit.items import AbstractTable

from daytona_upgrade._requirements import requirement_paths
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


def test_rewrites_used_binding_when_setup_name_is_reassigned_later() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n"
        "setup(install_requires=deps)\n"
        "deps = ['daytona-sdk==0.225']\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )


def test_rewrites_annotation_when_setup_binding_has_value() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps: list[str] = ['daytona-sdk==0.224']\n"
        "setup(install_requires=deps)\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )


@pytest.mark.parametrize(
    "filename,section",
    [
        ("pyproject.toml", "tool.poetry.dependencies"),
        ("Pipfile", "packages"),
    ],
)
@pytest.mark.parametrize(
    "entries",
    [
        "daytona-sdk = {version='0.224',optional=true}\ndaytona = {version='0.225',extras=['otel']}\n",
        "daytona = {version='0.225',extras=['otel']}\ndaytona-sdk = {version='0.224',optional=true}\n",
        "Daytona_SDK = '0.224'\ndaytona-sdk = '0.225'\n",
    ],
)
def test_preserves_declarations_when_package_names_collide(
    filename: str, section: str, entries: str
) -> None:
    # Given
    source = f"[{section}]\n{entries}"
    # When
    result = inspect_manifest(Path(filename), source, load_mapping())
    # Then
    assert result.source == source
    assert result.found
    assert not result.changes
    assert result.manual


def test_retains_constraint_mode_when_constraint_includes_requirement(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "requirements.txt"
    root.write_text("-c constraints\n")
    (tmp_path / "constraints").write_text("-r nested\n")
    nested = tmp_path / "nested"
    nested.write_text("daytona==0.224\n")
    # When
    result = requirement_paths(tmp_path, [root])
    # Then
    assert nested in result.constraints


def test_migrates_poetry_when_only_old_supported_tomlkit_api_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Table.item was introduced after the declared minimum tomlkit version.
    monkeypatch.delattr(AbstractTable, "item")
    source = "[tool.poetry.dependencies]\ndaytona-sdk = '0.224'\n"
    # When
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then
    assert tomlkit.parse(result.source).unwrap() == {
        "tool": {
            "poetry": {
                "dependencies": {"daytona": {"version": ">=2,<3", "extras": ["v0-sdk"]}}
            }
        }
    }
