# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Ancestor includes are requirements regardless of filename, never edit targets."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize("name", ["deps", "base.data", "pyproject.toml"])
@pytest.mark.parametrize("option", ["-r", "--requirement", "--requirement="])
def test_certifies_runtime_when_ancestor_include_has_arbitrary_name(
    tmp_path: Path, name: str, option: str
) -> None:
    # Given an ancestor runtime include chain with arbitrary filenames and a cycle.
    (tmp_path / ".git").mkdir()
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(f"{option}{'' if option.endswith('=') else ' '}deps.d/{name}\n")
    included = tmp_path / "deps.d"
    included.mkdir()
    dependency = included / name
    source = "-r nested\n"
    dependency.write_text(source)
    (included / "nested").write_text(f"daytona[v0-sdk]>=2,<3\n-r {name}\n")
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When upgrading a partial source root.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then runtime proof is recognized without migrating ancestor files.
    assert not any(item["kind"] == "manual-dependency" for item in result.items)
    assert [change.file.relative for change in result.changes] == ["app.py"]
    assert dependency.read_text() == source


@pytest.mark.parametrize("option", ["-c", "--constraint", "-r"])
def test_preserves_manual_review_when_include_is_not_runtime_proof(
    tmp_path: Path, option: str
) -> None:
    # Given a non-runtime declaration behind an arbitrary include filename.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text(f"{option} deps\n")
    (tmp_path / "deps").write_text("-r base.data\n")
    (tmp_path / "base.data").write_text(
        "daytona==0.224.0\n" if option == "-r" else "daytona[v0-sdk]>=2,<3\n"
    )
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting a partial root.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then constraints or old SDKs cannot certify installation of the v0 extra.
    assert any(item["kind"] == "manual-dependency" for item in result.items)


@pytest.mark.parametrize("link", ["escape", "symlink-file", "symlink-directory"])
def test_preserves_boundary_when_ancestor_include_escapes_or_uses_symlink(
    tmp_path: Path, link: str
) -> None:
    # Given an include whose modern SDK declaration is outside the readable boundary.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    outside = (tmp_path if link == "escape" else repo) / "outside"
    outside.mkdir()
    (outside / "deps").write_text("daytona[v0-sdk]>=2,<3\n")
    include = "../outside/deps"
    if link == "symlink-file":
        (repo / "deps").symlink_to(outside / "deps")
        include = "deps"
    elif link == "symlink-directory":
        (repo / "linked").symlink_to(outside, target_is_directory=True)
        include = "linked/deps"
    (repo / "requirements.txt").write_text(f"-r {include}\n")
    root = repo / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting an ancestor include graph.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then an unsafe path never supplies dependency proof.
    assert any(item["kind"] == "manual-dependency" for item in result.items)


@pytest.mark.parametrize("constraint", ["-c", "--constraint"])
def test_keeps_constraint_semantics_when_nested_file_is_also_required(
    tmp_path: Path, constraint: str
) -> None:
    # Given a modern dependency reached through runtime and constraint graphs.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text(f"-r base.data\n{constraint} deps\n")
    (tmp_path / "deps").write_text("-r base.data\n-c deps\n")
    (tmp_path / "base.data").write_text("daytona[v0-sdk]>=2,<3\n")
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When scanning both include contexts despite cycles.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then the constraint context survives nested requirement edges.
    assert any(item["kind"] == "manual-dependency" for item in result.items)


@pytest.mark.parametrize("option", ["-r", "-c"])
def test_reports_read_error_when_arbitrary_include_is_unreadable(
    tmp_path: Path, option: str
) -> None:
    # Given an included requirements file that is not UTF-8.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text(f"{option} deps\n")
    dependency = tmp_path / "deps"
    dependency.write_bytes(b"\xff")
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When reading either requirement or constraint includes.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then the unreadable included path is exposed rather than silently skipped.
    assert any(
        item["kind"] == "manual-dependency" and "../deps" in item["message"]
        for item in result.items
    )
