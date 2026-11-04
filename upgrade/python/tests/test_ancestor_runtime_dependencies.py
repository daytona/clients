# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only unconditional runtime ancestor dependencies certify partial upgrades."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "name,source",
    [
        ("requirements.txt", 'daytona[v0-sdk]>=2; python_version < "1"\n'),
        ("requirements.txt", 'daytona[v0-sdk]>=2; python_version >= "3"\n'),
        ("requirements-dev.in", "daytona[v0-sdk]>=2\n"),
        ("requirements-dev.txt", "daytona[v0-sdk]>=2\n"),
        ("constraints.in", "daytona[v0-sdk]>=2\n"),
        ("constraints.txt", "daytona[v0-sdk]>=2\n"),
        ("pyproject.toml", '[dependency-groups]\ndev=["daytona[v0-sdk]>=2"]\n'),
        (
            "pyproject.toml",
            '[project.optional-dependencies]\nlegacy=["daytona[v0-sdk]>=2"]\n',
        ),
        (
            "pyproject.toml",
            '[tool.poetry.group.dev.dependencies]\ndaytona={version=">=2",extras=["v0-sdk"]}\n',
        ),
        (
            "pyproject.toml",
            '[tool.poetry.dependencies]\ndaytona={version=">=2",extras=["v0-sdk"],markers="python_version >= \'3\'"}\n',
        ),
        (
            "pyproject.toml",
            '[tool.poetry.dependencies]\ndaytona={version=">=2",extras=["v0-sdk"],python=">=3"}\n',
        ),
        (
            "pyproject.toml",
            '[tool.poetry.dependencies]\ndaytona={version=">=2",extras=["v0-sdk"],optional=true}\n',
        ),
        ("Pipfile", '[dev-packages]\ndaytona={version=">=2",extras=["v0-sdk"]}\n'),
        (
            "Pipfile",
            '[packages]\ndaytona={version=">=2",extras=["v0-sdk"],os_name="posix"}\n',
        ),
        (
            "Pipfile",
            '[packages]\ndaytona={version=">=2",extras=["v0-sdk"],platform_machine="x86_64"}\n',
        ),
    ],
)
def test_dependency_stays_manual_when_parent_is_not_runtime_proof(
    tmp_path: Path, name: str, source: str
) -> None:
    # Given an ancestor declaration that cannot unconditionally install v0.
    (tmp_path / ".git").mkdir()
    (tmp_path / name).write_text(source)
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating only the source subtree.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then the consuming runtime requirement still needs verification.
    assert any(item["kind"] == "manual-dependency" for item in result.items)


@pytest.mark.parametrize("include", ["-c", "--constraint"])
@pytest.mark.parametrize("name", ["constraints.in", "requirements.in"])
def test_dependency_stays_manual_when_parent_only_constrains_sdk(
    tmp_path: Path, include: str, name: str
) -> None:
    # Given a constraint file reached from a runtime requirements manifest.
    (tmp_path / ".git").mkdir()
    (tmp_path / name).write_text("daytona[v0-sdk]>=2\n")
    (tmp_path / "requirements.txt").write_text(f"{include} {name}\n")
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating a partial root.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then constraints are not mistaken for an installing requirement.
    assert any(item["kind"] == "manual-dependency" for item in result.items)


def test_dependency_stays_manual_when_nearer_manifest_requires_zero_x(
    tmp_path: Path,
) -> None:
    # Given an outer modern declaration and a nearer owning old declaration.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2\n")
    app = tmp_path / "app"
    app.mkdir()
    (app / "pyproject.toml").write_text(
        '[project]\ndependencies=["daytona==0.224.0"]\n'
    )
    source = app / "main.py"
    source.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When targeting the single source file.
    result = preflight(source, TransformOptions(load_mapping()))
    # Then the farther parent cannot mask its nearer owner.
    assert any(item["kind"] == "manual-dependency" for item in result.items)


def test_dependency_read_failure_is_noted_when_parent_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an unreadable ancestor manifest (root-safe permission failure seam).
    (tmp_path / ".git").mkdir()
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("daytona[v0-sdk]>=2\n")
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    read_text = Path.read_text

    def read(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == manifest:
            raise PermissionError(13, "Permission denied", str(path))
        return read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", read)
    # When preflighting the subtree.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then the error is a report note, not a crash or silent proof.
    manual = [item for item in result.items if item["kind"] == "manual-dependency"]
    assert manual
    assert any("../requirements.txt" in item["message"] for item in manual)
