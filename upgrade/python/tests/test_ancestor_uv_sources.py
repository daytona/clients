# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Partial-root migrations report ancestor uv source overrides read-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize("package", ["daytona", "daytona-sdk", "daytona_sdk"])
@pytest.mark.parametrize("depth", [1, 2])
def test_reports_override_when_uv_sources_are_in_ancestor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], package: str, depth: int
) -> None:
    # Given a parent whose declared SDK 2.x dependency is overridden by a checkout.
    (tmp_path / ".git").mkdir()
    manifest = tmp_path / "pyproject.toml"
    source = (
        '[project]\ndependencies = ["daytona[v0-sdk]>=2,<3"]\n'
        f'[tool.uv.sources]\n{package} = {{ path = "../sdk" }}\n'
    )
    manifest.write_text(source)
    root = tmp_path.joinpath(*(["src"] * depth))
    root.mkdir(parents=True)
    app = root / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    expected = inspect_manifest(manifest, source, load_mapping()).manual

    # When writing only the source subtree under strict mode.
    status = main([str(root), "--force", "--yes", "--json", "--strict"])

    # Then the same override diagnostic as a local scan names the untouched parent.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [
        (item["line"], item["message"])
        for item in report["items"]
        if item["kind"] == "manual-dependency"
        and item["file"] == "../" * depth + "pyproject.toml"
    ] == list(expected)
    assert report["dependencies"] == []
    assert report["changedFiles"] == ["app.py"]
    assert app.read_text().endswith("Daytona().v0.create()\n")
    assert manifest.read_text() == source


def test_ignores_override_when_manifest_is_above_git_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an unrelated enclosing uv project and a valid runtime dependency in Git.
    parent = tmp_path / "pyproject.toml"
    source = '[tool.uv.sources]\ndaytona = { path = "../sdk" }\n'
    parent.write_text(source)
    repo = tmp_path / "repo"
    root = repo / "src"
    root.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: elsewhere\n")
    (repo / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")

    # When inspecting the subtree without crossing its Git boundary.
    status = main([str(root), "--dry", "--json", "--strict"])

    # Then the unrelated enclosing source override does not require manual work.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [item["kind"] for item in report["items"]] == ["types-unresolved"]
    assert parent.read_text() == source


def test_ignores_unrelated_ancestor_manifest_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a nested project with its own dependency under an outer Python floor.
    (tmp_path / ".git").mkdir()
    outer = tmp_path / "pyproject.toml"
    outer.write_text('[project]\nrequires-python = "<3.10"\n')
    project = tmp_path / "nested"
    project.mkdir()
    manifest = project / "pyproject.toml"
    manifest.write_text('[project]\ndependencies = ["daytona==0.224.0"]\n')
    app = project / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")

    # When upgrading the nested project through the strict CLI.
    status = main([str(project), "--force", "--yes", "--json", "--strict"])

    # Then only the nested dependency migrates, without inheriting the outer floor.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert report["dependencies"]
    assert app.read_text() == "from daytona import Daytona\nDaytona().v0.create()\n"
    assert outer.read_text() == '[project]\nrequires-python = "<3.10"\n'


@pytest.mark.parametrize(
    "case",
    [
        (target, override)
        for target in (".", "src", "src/app.py", "dependency-only")
        for override in (
            '{ path = "../sdk" }',
            '{ git = "https://example.com/sdk.git" }',
            '{ url = "https://example.com/daytona.whl" }',
        )
    ],
)
def test_reports_override_when_member_declares_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str, str]
) -> None:
    # Given a member dependency below two ancestor source overrides.
    target, override = case
    (tmp_path / ".git").mkdir()
    source = f"[tool.uv.sources]\ndaytona = {override}\n"
    parent = tmp_path / "pyproject.toml"
    parent.write_text(source)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nearer = workspace / "pyproject.toml"
    nearer.write_text(source)
    member = workspace / "member"
    root = member / "src"
    root.mkdir(parents=True)
    manifest = member / "pyproject.toml"
    manifest.write_text('[project]\ndependencies = ["daytona==0.224.0"]\n')
    app_source = (
        "from daytona import Daytona\nDaytona().v0.create()\n"
        if target == "dependency-only"
        else "from daytona import Daytona\nDaytona().create()\n"
    )
    (root / "app.py").write_text(app_source)

    # When migrating the member, its source subtree, or one source file.
    path = member if target == "dependency-only" else member / target
    status = main([str(path), "--force", "--yes", "--json", "--strict"])

    # Then every ancestor override is reported once despite the nearer declaration.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    manual = [item for item in report["items"] if item["kind"] == "manual-dependency"]
    depth = 2 if target in (".", "dependency-only") else 3
    assert sum(item["file"] == "../" * depth + "pyproject.toml" for item in manual) == 1
    assert (
        sum(item["file"] == "../" * (depth - 1) + "pyproject.toml" for item in manual)
        == 1
    )
    assert bool(report["dependencies"]) == (target in (".", "dependency-only"))
    assert manifest.read_text() == (
        '[project]\ndependencies = ["daytona[v0-sdk]>=2,<3"]\n'
        if target in (".", "dependency-only")
        else '[project]\ndependencies = ["daytona==0.224.0"]\n'
    )
    assert parent.read_text() == source
    assert nearer.read_text() == source


@pytest.mark.parametrize("scope", [".", "members"])
def test_reports_override_once_when_ancestor_scan_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], scope: str
) -> None:
    # Given two rewritten member projects under an already-discovered uv override.
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.sources]\ndaytona = { path = "../sdk" }\n'
    )
    members = tmp_path / "members"
    members.mkdir()
    for name in ("one", "two"):
        member = members / name
        member.mkdir()
        (member / "pyproject.toml").write_text(
            '[project]\ndependencies = ["daytona==0.224.0"]\n'
        )
        (member / "app.py").write_text(
            "from daytona import Daytona\nDaytona().create()\n"
        )

    # When scanning the whole project, including both members and their ancestor.
    status = main([str(tmp_path / scope), "--dry", "--json", "--strict"])

    # Then local and repeated ancestor inspection produce a single diagnostic.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    manual = [item for item in report["items"] if item["kind"] == "manual-dependency"]
    assert len(manual) == 1
    assert manual[0]["file"] == (
        "pyproject.toml" if scope == "." else "../pyproject.toml"
    )
