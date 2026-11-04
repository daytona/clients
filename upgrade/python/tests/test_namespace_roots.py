# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Refuse local SDK namespace collisions before changing project files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "relative",
    [
        f"{root}/{name}/{file}"
        for root in (".", "src", "lib")
        for name in ("daytona", "daytona_sdk")
        for file in ("helpers.py", "nested/helpers.py", "helpers.pyi")
    ],
)
def test_upgrade_refuses_when_import_root_contains_sdk_namespace_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], relative: str
) -> None:
    # Given a local namespace portion, including an explicitly configured root.
    local = tmp_path / relative
    local.parent.mkdir(parents=True)
    local.write_text("def helper(): return 1\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("daytona-sdk==0.224.0\n")
    if relative.startswith("lib/"):
        (tmp_path / "pyproject.toml").write_text(
            '[tool.setuptools.packages.find]\nwhere = ["lib"]\n'
        )
    app = tmp_path / "app.py"
    name = "daytona_sdk" if "daytona_sdk" in relative else "daytona"
    source = f"from {name}.helpers import helper\n"
    app.write_text(source)
    # When upgrading through the real CLI with writes enabled.
    status = main([str(tmp_path), "--force", "--json"])
    # Then the collision refuses atomically rather than breaking local imports.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "local-sdk-module"
    assert report["changedFiles"] == []
    assert app.read_text() == source
    assert manifest.read_text() == "daytona-sdk==0.224.0\n"


@pytest.mark.parametrize(
    "layout",
    [
        (manifest, package_file)
        for manifest in (
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "Pipfile",
        )
        for package_file in ("__init__.py", "helpers.py")
    ],
)
def test_upgrade_refuses_when_service_manifest_defines_import_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    layout: tuple[str, str],
) -> None:
    # Given a standalone service invoked from a repository-level upgrade.
    manifest, package_file = layout
    service = tmp_path / "svc"
    local = service / "daytona" / package_file
    local.parent.mkdir(parents=True)
    local.write_text("X = 1\n")
    (service / manifest).write_text("")
    app = service / "app.py"
    source = "from daytona import X\n"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.224.0\n")
    # When running from the repository root instead of inside the service.
    status = main([str(tmp_path), "--force", "--json"])
    # Then the local import is refused, not rewritten as an SDK import.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "local-sdk-module"
    assert app.read_text() == source


@pytest.mark.parametrize("name", ["daytona", "daytona_sdk"])
def test_upgrade_ignores_when_sdk_directory_has_no_python_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given charts or docs whose directory happens to share the SDK name.
    local = tmp_path / name
    local.mkdir()
    (local / "values.yaml").write_text("replicas: 1\n")
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.224.0\n")
    # When upgrading the containing project.
    status = main([str(tmp_path), "--force", "--json"])
    # Then a non-Python directory does not block dependency migration.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert report["changedFiles"] == ["requirements.txt"]
