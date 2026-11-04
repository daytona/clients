# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency preflight preserves the real paths needed by package indexing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


def test_rewrites_reexport_when_index_receives_real_package_filename(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    package = tmp_path / "src/pkg"
    package.mkdir(parents=True)
    (package / "base.py").write_text(
        "from daytona import Daytona\nclient = Daytona()\n"
        "def factory() -> Daytona:\n    return Daytona()\n"
    )
    (package / "__init__.py").write_text("from .base import client, factory\n")
    app = tmp_path / "src/app.py"
    app.write_text(
        "from pkg import client, factory\nclient.create()\nfactory().create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224\n")
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert app.read_text() == (
        "from pkg import client, factory\nclient.v0.create()\nfactory().v0.create()\n"
    )
    assert "src/app.py" in report["changedFiles"]


def test_requirement_only_siblings_have_independent_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    package_a = tmp_path / "a"
    package_b = tmp_path / "b"
    package_a.mkdir()
    package_b.mkdir()
    requirements_a = package_a / "requirements.txt"
    requirements_a.write_text("daytona==0.224.0\n")
    (package_a / "app.py").write_text(
        "from daytona import Daytona\nd = Daytona()\nd.create()\n"
    )
    (package_b / "requirements.txt").write_text("requests\n")
    (package_b / "app.py").write_text(
        "from daytona import Daytona\nd = Daytona()\nd.create()\n"
    )
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert requirements_a.read_text() == "daytona[v0-sdk]>=2,<3\n"
    assert (package_a / "app.py").read_text().endswith("d.v0.create()\n")
    assert (package_b / "app.py").read_text().endswith("d.v0.create()\n")
    assert any(
        item["kind"] == "manual-dependency" and item["file"] == "b/app.py"
        for item in report["items"]
    )


def test_recognizes_dependency_when_include_already_current(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "requirements.txt").write_text("--requirement deps\n")
    (tmp_path / "deps").write_text("daytona[v0-sdk]>=2,<3\n")
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["changedFiles"] == []
    assert report["dependencies"] == []
    assert report["items"] == []


def test_reports_extras_requirement_when_dependency_is_constraint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "requirements.txt").write_text("-c pins\n")
    dependency = tmp_path / "pins"
    dependency.write_text("daytona==0.224 ; python_version >= '3.9'\n")
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert dependency.read_text() == "daytona>=2,<3 ; python_version >= '3.9'\n"
    assert [item["kind"] for item in report["items"]] == ["manual-dependency"]
    assert "daytona[v0-sdk]>=2,<3" in report["items"][0]["message"]
