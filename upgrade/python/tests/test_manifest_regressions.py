# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency-reference and setuptools compatibility regressions."""

from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path

import pytest
import tomlkit
from packaging.requirements import Requirement

from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize("name", ["daytona-sdk", "Daytona_SDK", "DAYTONA.SDK"])
def test_rewrites_all_extra_references_when_poetry_dependency_is_renamed(
    name: str,
) -> None:
    # Given optional dependency references using normalized historical names.
    source = (
        '[project.optional-dependencies]\nagent = ["daytona_sdk==0.224"]\n'
        "[tool.poetry.dependencies]\n"
        f'"{name}" = {{version = "0.224", optional = true}}\n'
        "[tool.poetry.extras]\n"
        'agent = ["daytona-sdk", \'Daytona_SDK\', "other"] # keep\n'
        'all = ["DAYTONA.SDK"]\n'
    )
    # When migrating the declaration and its references.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then each extra resolves to the migrated dependency, preserving optionality.
    document = tomlkit.parse(result.source).unwrap()
    dependencies = document["tool"]["poetry"]["dependencies"]
    assert dependencies["daytona"]["optional"] is True
    assert document["tool"]["poetry"]["extras"] == {
        "agent": ["daytona", "daytona", "other"],
        "all": ["daytona"],
    }
    requirement = Requirement(document["project"]["optional-dependencies"]["agent"][0])
    assert requirement.name == "daytona"
    assert "2.0" in requirement.specifier
    assert "0.224" not in requirement.specifier
    assert requirement.extras == {"v0-sdk"}
    assert "# keep" in result.source
    assert "'daytona'" in result.source
    assert result.manual == ()


@pytest.mark.parametrize(
    "extras", ['agent = "daytona-sdk"', "agent = [1]", 'agent = ["daytona-sdk>=0"]']
)
def test_preserves_manifest_when_poetry_extra_reference_is_unsupported(
    extras: str,
) -> None:
    # Given an unparseable extras mapping that cannot safely follow a key rename.
    source = (
        '[tool.poetry.dependencies]\ndaytona-sdk = {version="0.224", optional=true}\n'
        f"[tool.poetry.extras]\n{extras}\n"
    )
    # When inspecting the manifest.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then neither side of the reference is changed and manual work is reported.
    assert result.source == source
    assert result.changes == ()
    assert result.manual
    assert not result.v2_found


def test_preserves_manifest_when_poetry_extras_table_is_unsupported() -> None:
    # Given malformed extras that cannot safely follow an optional key rename.
    source = (
        '[tool.poetry]\nextras = ["daytona-sdk"]\n'
        '[tool.poetry.dependencies]\ndaytona-sdk = {version="0.224", optional=true}\n'
    )
    # When inspecting the whole manifest.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then both the declaration and its references remain manual.
    assert result.source == source
    assert result.changes == ()
    assert result.manual


def test_keeps_poetry_extra_reference_when_only_group_is_renamed() -> None:
    # Given a preserved main source and a separate rewritable development pin.
    source = (
        "[tool.poetry.dependencies]\n"
        'daytona-sdk = {git="https://example.test/sdk", subdirectory="sdk-python", optional=true}\n'
        '[tool.poetry.extras]\nagent = ["daytona-sdk"]\n'
        '[tool.poetry.group.dev.dependencies]\ndaytona-sdk = "0.224"\n'
    )
    # When migrating the independently supported dependency group.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the extra still points to the preserved main declaration.
    document = tomlkit.parse(result.source).unwrap()
    assert document["tool"]["poetry"]["extras"]["agent"] == ["daytona-sdk"]
    assert "daytona-sdk" in document["tool"]["poetry"]["dependencies"]
    assert "daytona" in document["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert result.manual


def test_migrates_extra_pin_when_setup_cfg_has_optional_dependency() -> None:
    # Given an optional pin that would conflict with the migrated runtime pin.
    extra = 'daytona-sdk==0.224 ; python_version >= "3.10" # keep'
    source = (
        "[options]\ninstall_requires = daytona==0.224\n"
        "[options.extras_require]\nagent =\n    " + extra + "\n"
    )
    # When migrating the INI dependency options.
    result = inspect_manifest(Path("setup.cfg"), source, load_mapping())
    # Then the base and optional requirements admit the same SDK 2.x release.
    parser = ConfigParser(inline_comment_prefixes=("#",))
    parser.read_string(result.source)
    for value in (
        parser["options"]["install_requires"],
        parser["options.extras_require"]["agent"],
    ):
        requirement = Requirement(value.strip())
        assert requirement.name == "daytona"
        assert "2.0" in requirement.specifier
        assert "0.224" not in requirement.specifier
        assert requirement.extras == {"v0-sdk"}
    assert "# keep" in result.source
    assert 'python_version >= "3.10"' in result.source


def test_keeps_runtime_proof_false_when_setup_cfg_dependency_is_only_optional() -> None:
    # Given an optional-only declaration already requiring SDK 2.x.
    source = "[options.extras_require]\nagent = daytona[v0-sdk]>=2,<3\n"
    # When recognizing that declaration.
    result = inspect_manifest(Path("setup.cfg"), source, load_mapping())
    # Then the extra is recognized but cannot certify a runtime installation.
    assert result.found
    assert not result.v2_found


@pytest.mark.parametrize("alternatives", [False, True])
def test_preserves_dependency_when_poetry_source_has_subdirectory(
    alternatives: bool,
) -> None:
    # Given a Git-only field that cannot survive conversion to a registry table.
    dependency = (
        '{git="https://example.test/sdk", subdirectory="sdk-python", optional=true}'
    )
    if alternatives:
        dependency = '[{version="0.224", python=">=3.10"}, ' + dependency + "]"
    source = "[tool.poetry.dependencies]\ndaytona-sdk = " + dependency + "\n"
    # When inspecting the source declaration.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then all source alternatives remain unchanged with an explicit manual task.
    assert result.source == source
    assert result.changes == ()
    assert result.found
    assert result.manual
    assert not result.v2_found


@pytest.mark.parametrize("name", ["setup.py", "setup.cfg"])
@pytest.mark.parametrize("constraint", [">=3.9", ">=3.10", "unknown"])
def test_reports_compatibility_when_setuptools_python_range_needs_review(
    name: str, constraint: str
) -> None:
    # Given a setuptools interpreter constraint alongside an old SDK pin.
    source = (
        f'from setuptools import setup\nsetup(python_requires="{constraint}", install_requires=["daytona==0.224"])\n'
        if name == "setup.py"
        else f"[options]\npython_requires = {constraint}\ninstall_requires = daytona==0.224\n"
    )
    # When inspecting supported interpreter compatibility.
    result = inspect_manifest(Path(name), source, load_mapping())
    # Then unsafe or unknown ranges report compatibility work without changing it.
    assert len(result.manual) == int(constraint != ">=3.10")
    assert constraint in result.source
    assert result.changes


@pytest.mark.parametrize("expression", ["floor", "get_floor()"])
def test_reports_compatibility_when_setup_python_range_is_indirect(
    expression: str,
) -> None:
    # Given either a static variable or an unresolved function result as the floor.
    source = (
        'from setuptools import setup\nfloor = ">=3.9"\n'
        f'setup(python_requires={expression}, install_requires=["daytona==0.224"])\n'
    )
    # When inspecting setuptools without executing project code.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then both unsupported and unknown interpreter constraints remain visible.
    assert len(result.manual) == 1
    assert result.changes


@pytest.mark.parametrize("indent", ["", "  "])
def test_migrates_dependency_when_setup_cfg_python_floor_precedes_it(
    indent: str,
) -> None:
    # Given equally indented options, with a multiline interpreter constraint.
    source = (
        f"[options]\n{indent}python_requires =\n{indent}    >=3.10\n"
        f"{indent}install_requires = daytona==0.224\n"
    )
    # When scanning the floor without consuming the following dependency option.
    result = inspect_manifest(Path("setup.cfg"), source, load_mapping())
    # Then the supported range stays clean and the runtime dependency migrates.
    assert result.manual == ()
    assert f"{indent}install_requires = daytona[v0-sdk]>=2,<3" in result.source
