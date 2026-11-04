# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Parent SDK 2.x dependencies satisfy the partial-root upgrade guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "manifest",
    [
        ("requirements.txt", "daytona[v0-sdk]>=2,<3\n"),
        ("requirements.in", "daytona[v0-sdk]>=2\n"),
        ("pyproject.toml", '[project]\ndependencies = ["daytona[v0-sdk]>=2.1"]\n'),
        (
            "pyproject.toml",
            '[tool.poetry.dependencies]\ndaytona = {version = "^2.0", extras = ["v0-sdk"]}\n',
        ),
        ("Pipfile", '[packages]\ndaytona = {version = ">=2", extras = ["v0-sdk"]}\n'),
        ("setup.cfg", "[options]\ninstall_requires =\n    daytona[v0-sdk]>=2\n"),
        (
            "setup.py",
            'from setuptools import setup\nsetup(install_requires=["daytona[v0-sdk]>=2"])\n',
        ),
    ],
)
@pytest.mark.parametrize("depth", [1, 2])
def test_skips_manual_dependency_when_parent_requires_v2(
    tmp_path: Path, manifest: tuple[str, str], depth: int
) -> None:
    # Given a partial-root run below an already upgraded parent manifest.
    (tmp_path / ".git").mkdir()
    name, source = manifest
    declaration = tmp_path / name
    declaration.write_text(source)
    root = tmp_path.joinpath(*(["src"] * depth))
    root.mkdir(parents=True)
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting only the source subtree.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then no dependency action is requested and the parent is never scheduled for edits.
    assert not any(item["kind"] == "manual-dependency" for item in result.items)
    assert [change.file.relative for change in result.changes] == ["app.py"]
    assert declaration.read_text() == source


@pytest.mark.parametrize(
    "requirement", ["daytona<2", "daytona>=1", "daytona", "other>=2"]
)
def test_keeps_manual_dependency_when_parent_does_not_require_v2(
    tmp_path: Path, requirement: str
) -> None:
    # Given an old, unconstrained, or unrelated parent dependency.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text(requirement + "\n")
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting the subtree.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then an out-of-scope old dependency is not treated as SDK 2.x proof.
    expected = ["manual-dependency"]
    if requirement.startswith("daytona"):
        expected.append("types-unresolved")
    assert [item["kind"] for item in result.items] == expected


def test_keeps_manual_dependency_when_modern_manifest_is_above_git_root(
    tmp_path: Path,
) -> None:
    # Given a modern manifest belonging to a different enclosing repository.
    (tmp_path / "requirements.txt").write_text("daytona>=2\n")
    repo = tmp_path / "repo"
    root = repo / "src"
    root.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: elsewhere\n")
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting inside the nested worktree.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then the scan stops at its .git file, not at the enclosing project.
    assert [item["kind"] for item in result.items] == ["manual-dependency"]


@pytest.mark.parametrize(
    "manifest,source",
    [
        ("pyproject.toml", "[broken"),
        ("setup.py", "setup("),
        ("requirements.txt", "\udcff"),
    ],
)
def test_keeps_manual_dependency_when_parent_manifest_cannot_be_parsed(
    tmp_path: Path, manifest: str, source: str
) -> None:
    # Given an out-of-scope manifest that cannot provide dependency evidence.
    (tmp_path / ".git").mkdir()
    (tmp_path / manifest).write_bytes(source.encode("utf-8", errors="surrogateescape"))
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting the partial root.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then unreadable evidence does not prevent the usual manual fallback.
    assert [item["kind"] for item in result.items] == ["manual-dependency"] * 2
    assert result.items[0]["file"] == "../" + manifest
