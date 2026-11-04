# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Setuptools ancestor certification requires unconditional runtime requirements."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "declaration",
    [
        'setup(extras_require={"testing": ["daytona[v0-sdk]>=2"]})',
        'setup(tests_require=["daytona[v0-sdk]>=2"])',
        'setup(setup_requires=["daytona[v0-sdk]>=2"])',
        'setup(install_requires=["daytona==0.224.0"], extras_require={"testing": ["daytona[v0-sdk]>=2"]})',
        'if False:\n    setup(install_requires=["daytona[v0-sdk]>=2"])',
        'if enabled:\n    setup(install_requires=["daytona[v0-sdk]>=2"])',
        'def package():\n    setup(install_requires=["daytona[v0-sdk]>=2"])',
        'False and setup(install_requires=["daytona[v0-sdk]>=2"])',
        'setup(install_requires=["daytona[v0-sdk]>=2"]) if flag else None',
        '[setup(install_requires=["daytona[v0-sdk]>=2"]) for _ in []]',
        'setup(install_requires=["daytona==0.224.0", "daytona[v0-sdk]>=2"])',
    ],
)
def test_dependency_stays_manual_when_setup_is_not_runtime_proof(
    tmp_path: Path, declaration: str
) -> None:
    # Given an ancestor that cannot guarantee installation of the bundled v0 SDK.
    (tmp_path / ".git").mkdir()
    manifest = tmp_path / "setup.py"
    source = "from setuptools import setup\n" + declaration + "\n"
    manifest.write_text(source)
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When only the source subtree is migrated.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then source changes cannot silently claim the runtime dependency is ready.
    assert any(item["kind"] == "manual-dependency" for item in result.items)
    assert manifest.read_text() == source


@pytest.mark.parametrize(
    "declaration",
    [
        'setup(install_requires=["daytona[v0-sdk]>=2"])',
        'runtime = ["daytona[v0-sdk]>=2"]\nsetup(install_requires=runtime)',
        'import setuptools as packaging\npackaging.setup(install_requires=["daytona[v0-sdk]>=2"])',
    ],
)
def test_dependency_is_certified_when_setup_runtime_is_unconditional(
    tmp_path: Path, declaration: str
) -> None:
    # Given a static unconditional module-level installing declaration.
    (tmp_path / ".git").mkdir()
    (tmp_path / "setup.py").write_text("from setuptools import setup\n" + declaration)
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating only the consumer's source.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then an existing runtime dependency is enough without editing the parent.
    assert not any(item["kind"] == "manual-dependency" for item in result.items)
