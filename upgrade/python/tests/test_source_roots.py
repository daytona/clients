# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Packaging roots bound SDK package ownership for atomic upgrades."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("name", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize(
    "config,relative",
    [
        (
            '[tool.setuptools.packages.find]\nwhere = ["lib"]\n',
            "lib/{name}/__init__.py",
        ),
        (
            '[tool.setuptools.packages.find]\nwhere = ["lib"]\n',
            "src/{name}/__init__.py",
        ),
        ('[tool.setuptools.package-dir]\n"" = "lib"\n', "lib/{name}.py"),
        (
            '[tool.setuptools.package-dir]\n"{name}" = "lib/owned"\n',
            "lib/owned/__init__.py",
        ),
        (
            '[tool.poetry]\npackages = [{{include = "{name}", from = "lib"}}]\n',
            "lib/{name}.py",
        ),
        (
            '[tool.hatch.build.targets.wheel]\npackages = ["lib/{name}"]\n',
            "lib/{name}/__init__.py",
        ),
        ('[tool.hatch.build.targets.wheel]\nsources = ["lib"]\n', "lib/{name}.py"),
        ('[tool.hatch.build.targets.wheel.sources]\n"lib" = ""\n', "lib/{name}.py"),
    ],
)
def test_refuses_when_sdk_ownership_is_local(
    tmp_path: Path, name: str, config: str, relative: str
) -> None:
    # Given configured source roots containing a colliding module or package.
    local = tmp_path / relative.format(name=name)
    local.parent.mkdir(parents=True)
    local.write_text("class Daytona: pass\n")
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(config.format(name=name))
    app = tmp_path / "app.py"
    source = f"from {name} import Daytona\nDaytona().create()\n"
    app.write_text(source)
    # When the real CLI would otherwise migrate imports and client accesses.
    status = main([str(tmp_path), "--force", "--json"])
    # Then refusal is atomic for import-root collisions.
    assert status == 2
    assert app.read_text() == source
    assert manifest.read_text() == config.format(name=name)


def test_upgrades_when_sdk_directory_has_no_discovered_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an empty directory that cannot shadow the installed regular package.
    (tmp_path / "lib/daytona").mkdir(parents=True)
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    # When preflight examines the scanned tree.
    status = main([str(tmp_path), "--force", "--json"])
    # Then a non-Python directory does not block migration or add review items.
    assert status == 0
    assert all(
        item["kind"] != "private-import"
        for item in json.loads(capsys.readouterr().out)["items"]
    )


@pytest.mark.parametrize(
    "directory",
    [".venv", "venv", "site-packages", "node_modules", ".tox", "build", "dist"],
)
def test_upgrades_when_sdk_package_is_under_ignored_directory(
    tmp_path: Path, directory: str
) -> None:
    # Given vendored SDK packages beneath a generated or installed tree.
    local = tmp_path / directory / "daytona"
    local.mkdir(parents=True)
    (local / "__init__.py").write_text("class Daytona: pass\n")
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When the CLI scans project-owned sources only.
    status = main([str(tmp_path), "--force", "--json"])
    # Then installed packages do not cause a false refusal.
    assert status == 0
    assert "Daytona().v0.create()" in app.read_text()


@pytest.mark.parametrize(
    "sibling",
    [
        "daytona/__init__.py",
        "daytona.py",
        "daytona_sdk.py",
        "daytona.pyi",
        "daytona_sdk.pyi",
    ],
)
def test_single_file_run_refuses_a_sibling_sdk_module(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], sibling: str
) -> None:
    # Given a script whose directory holds a module named like the SDK.
    (tmp_path / sibling).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / sibling).write_text("class Daytona: ...\n")
    script = tmp_path / "app.py"
    script.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When only the script is upgraded.
    status = main([str(script), "--dry", "--json"])
    # Then the sibling would shadow the SDK, so the run is refused.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "local-sdk-module"
