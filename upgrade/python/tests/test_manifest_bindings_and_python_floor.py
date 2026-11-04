# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Manifest bindings preserve dependency edits and interpreter constraints."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade import cli
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "declaration",
    ["deps: list[str]\n", "if flag:\n    deps: list[str]\n"],
)
def test_rewrites_binding_when_later_annotation_has_no_value(declaration: str) -> None:
    # Given an annotation that does not change the runtime dependency binding.
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n"
        + declaration
        + "setup(install_requires=deps)\n"
    )
    # When inspecting the static setup dependency.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then the original literal is migrated without inventing uncertainty.
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )
    assert result.manual == ()


@pytest.mark.parametrize(
    "body",
    ["class C:\n    pass\n", "class C:\n    setup(install_requires=deps)\n"],
)
def test_rewrites_binding_when_unrelated_class_executes(body: str) -> None:
    # Given a class definition that leaves the module dependency binding intact.
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n" + body + "setup(install_requires=deps)\n"
    )
    # When inspecting setup calls during and after class execution.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then the stable module literal remains available at each call.
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )
    assert result.manual == ()


@pytest.mark.parametrize("field", ["python_version", "python_full_version"])
@pytest.mark.parametrize("version", ["3.9", "3.9.23", "3.10", "3.13.1"])
def test_reports_python_floor_when_pipfile_requires_unsupported_python(
    field: str, version: str
) -> None:
    # Given a Pipfile with an explicit interpreter and an old SDK dependency.
    source = f'[requires]\n{field} = "{version}"\n[packages]\ndaytona = "==0.224"\n'
    # When inspecting the Pipfile's interpreter compatibility.
    result = inspect_manifest(Path("Pipfile"), source, load_mapping())
    # Then only unsupported interpreters receive the existing compatibility task.
    assert len(result.manual) == int(version.startswith("3.9"))
    assert result.changes


@pytest.mark.parametrize(
    "dependency",
    [
        "other @ https://example.test/daytona/package.whl",
        "other @ https://example.test/repo?project=daytona",
        "git+https://github.com/daytona/sdk.git",
        "-e git+https://github.com/daytona/sdk.git#egg=other",
        "https://example.test/daytona/sdk.whl",
        "git+https://example.test/repo#egg=daytona-tools",
        "# git+https://example.test/repo#egg=daytona",
        "other @ https://example.test/repo.whl # mirror #egg=daytona",
    ],
)
def test_ignores_dependency_when_only_url_mentions_daytona(dependency: str) -> None:
    # Given a direct URL whose path is not a declared Daytona package name.
    source = dependency + "\n"
    # When inspecting the dependency record.
    result = inspect_manifest(Path("requirements.txt"), source, load_mapping())
    # Then no Daytona dependency or manual migration item is invented.
    assert not result.found
    assert result.manual == ()
    assert result.source == source


@pytest.mark.parametrize("name", ["daytona", "daytona-sdk", "Daytona_SDK"])
def test_reports_dependency_when_url_has_explicit_daytona_egg(name: str) -> None:
    # Given legacy URL metadata explicitly naming a supported old SDK package.
    source = f"-e git+https://example.test/other/repo#egg={name}\n"
    # When inspecting the unsupported editable dependency declaration.
    result = inspect_manifest(Path("requirements.txt"), source, load_mapping())
    # Then the dependency remains manual rather than being lost or auto-rewritten.
    assert result.found
    assert len(result.manual) == 1
    assert result.source == source


def test_refuses_project_when_only_url_path_mentions_daytona(tmp_path: Path) -> None:
    # Given a dependency-only project with no declared Daytona dependency.
    source = "other @ https://example.test/daytona/package.whl\n"
    (tmp_path / "requirements.txt").write_text(source)
    # When running the real CLI without writing changes.
    status = cli.main([str(tmp_path), "--dry", "--yes"])
    # Then a repository URL cannot bypass the no-Daytona refusal.
    assert status == 2
