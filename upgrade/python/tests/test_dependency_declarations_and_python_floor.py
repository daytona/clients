# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency declarations must never disappear from manual migration reports."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade import cli
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


def test_bom_pin_is_migrated_when_requirements_are_utf8(tmp_path: Path) -> None:
    # Given pip-compatible Windows bytes, including the UTF-8 BOM and CRLF.
    path = tmp_path / "requirements.txt"
    path.write_bytes(b"\xef\xbb\xbfdaytona==0.224.0\r\n")
    # When invoking the CLI on a dependency-only project.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then recognition and writing preserve the original byte framing.
    assert status == 0
    assert path.read_bytes() == b"\xef\xbb\xbfdaytona[v0-sdk]>=2,<3\r\n"


@pytest.mark.parametrize(
    "dependency",
    [
        "-e git+https://example.test/repo#egg=daytona",
        "--editable ./sdk#egg=daytona-sdk",
        "git+https://example.test/repo#egg=daytona_sdk",
        "https://example.test/sdk.whl#egg=daytona",
        "daytona unsupported-syntax",
        "daytona => 0.2",
    ],
)
@pytest.mark.parametrize(
    "name", ["requirements.txt", "pyproject.toml", "setup.cfg", "setup.py"]
)
def test_dependency_is_manual_when_declaration_cannot_be_rewritten(
    name: str, dependency: str
) -> None:
    # Given an unsupported dependency record in any supported string container.
    source = {
        "requirements.txt": dependency + "\n",
        "pyproject.toml": f"[project]\ndependencies=[{dependency!r}]\n",
        "setup.cfg": f"[options]\ninstall_requires =\n    {dependency}\n",
        "setup.py": f"from setuptools import setup\nsetup(install_requires=[{dependency!r}])\n",
    }[name]
    # When inspecting dependency syntax.
    result = inspect_manifest(Path(name), source, load_mapping())
    # Then the declaration is recognized, unchanged, and explicitly user-owned.
    assert result.found
    assert result.source == source
    assert len(result.manual) == 1


@pytest.mark.parametrize(
    "constraint",
    [">=3.9", "^3.9", ">=3.8,<4", "~3.9", "3.9.*", ">=3.9,!=3.9.0", ">=3.9.99", "*"],
)
@pytest.mark.parametrize("poetry", [False, True])
def test_python_floor_is_manual_when_sdk2_does_not_support_every_allowed_python(
    constraint: str, poetry: bool
) -> None:
    # Given a project claiming support for an interpreter below SDK 2.x's floor.
    source = (
        f'[tool.poetry.dependencies]\npython = "{constraint}"\ndaytona = "^0.224"\n'
        if poetry
        else f'[project]\nrequires-python = "{constraint}"\ndependencies = ["daytona==0.224"]\n'
    )
    # When inspecting the project compatibility declaration.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then upgrading the SDK carries an explicit interpreter compatibility task.
    assert len(result.manual) == 1
    assert "3.10+" in result.manual[0][1]


@pytest.mark.parametrize(
    "constraint", [">=3.10", "^3.10", "~3.11", ">=3.9,!=3.9.*", ">=3.12,<4"]
)
def test_python_floor_needs_no_warning_when_only_sdk2_interpreters_are_allowed(
    constraint: str,
) -> None:
    # Given a Poetry project already constrained to supported interpreters.
    source = (
        f'[tool.poetry.dependencies]\npython = "{constraint}"\ndaytona = "^0.224"\n'
    )
    # When inspecting its minimum Python version.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then no compatibility action is invented.
    assert result.manual == ()
