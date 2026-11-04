# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Project diagnostics retain omitted-context and checkout-boundary details."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.partial_analysis import partial_analysis


@pytest.mark.parametrize("name", ["setup.py", "app.py"])
def test_reports_one_line_when_python_source_is_broken(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given malformed source that can also be a dependency manifest.
    path = tmp_path / name
    source = "from daytona import Daytona\nunfinished = (\n"
    path.write_text(source)
    # When the CLI preflights the project.
    status = main([str(tmp_path), "--force", "--json"])
    # Then one concise diagnostic identifies the file and parser location.
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert status == 1
    assert captured.err == ""
    assert len(report["items"]) == 1
    item = report["items"][0]
    assert item["kind"] == "unsupported-file"
    assert item["file"] == name
    assert item["line"] == 2
    assert item["message"].startswith("Could not be parsed: ")
    assert name not in item["message"]
    assert " (line 2)" not in item["message"]
    assert "\n" not in item["message"]
    assert "unfinished" not in item["message"]
    assert path.read_text() == source


def test_reports_target_without_errno_when_write_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a replacement failure whose OS filename is a staging file.
    directory = tmp_path / "src"
    directory.mkdir()
    path = directory / "app.py"
    path.write_text("from daytona import Daytona\nDaytona().create()\n")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError(errno.EACCES, "Permission denied", str(source))

    monkeypatch.setattr(os, "replace", fail_replace)
    # When the CLI applies its prepared outputs.
    status = main([str(tmp_path), "--force", "--json"])
    # Then both channels retain the target without an errno or temporary path.
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    expected = "daytona-upgrade: src/app.py could not be written: Permission denied; fix the problem, then rerun."
    assert status == 1
    assert captured.err == expected + "\n"
    assert report["nextSteps"][-1] == expected


@pytest.mark.parametrize(
    "name,source,expected",
    [
        ("pyproject.toml", '[project]\nname="app"\n', False),
        ("pyproject.toml", '[project]\ndependencies=["requests", "daytona<1"]\n', True),
        ("requirements.txt", "requests>=2\n# daytona<1\n", False),
        ("requirements.txt", "daytona<1\n", True),
        ("setup.cfg", "[options]\ninstall_requires = requests\n", False),
        ("setup.cfg", "[options]\ninstall_requires = daytona<1\n", True),
        (
            "setup.py",
            'from setuptools import setup\nsetup(name="daytona-helper")\n',
            False,
        ),
        (
            "setup.py",
            'from setuptools import setup\nsetup(install_requires=["daytona<1"])\n',
            True,
        ),
    ],
)
def test_limits_partial_notice_to_dependency_evidence(
    tmp_path: Path, name: str, source: str, expected: bool
) -> None:
    # Given a checkout ancestor manifest and a selected service.
    (tmp_path / ".git").mkdir()
    (tmp_path / name).write_text(source)
    selected = tmp_path / "service"
    selected.mkdir()
    # When inspecting omitted context.
    items = partial_analysis(selected)
    # Then only a declared Daytona dependency is evidence.
    assert bool(items) is expected


@pytest.mark.parametrize("git", [False, True])
@pytest.mark.parametrize("distance", [5, 6])
def test_bounds_ancestor_manifest_walk(
    tmp_path: Path, git: bool, distance: int
) -> None:
    # Given a manifest five or six parents above the selection.
    if git:
        (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("daytona<1\n")
    selected = tmp_path.joinpath(*[f"level{index}" for index in range(distance)])
    selected.mkdir(parents=True)
    # When inspecting ancestors with or without a checkout boundary.
    items = partial_analysis(selected)
    # Then checkout roots are included, while non-Git walking stops at five.
    assert bool(items) is (git or distance <= 5)


def test_stops_at_git_root_when_dependency_is_above_checkout(tmp_path: Path) -> None:
    # Given an unrelated dependency outside the checkout.
    (tmp_path / "requirements.txt").write_text("daytona<1\n")
    checkout = tmp_path / "checkout"
    selected = checkout / "service"
    selected.mkdir(parents=True)
    (checkout / ".git").mkdir()
    # When inspecting omitted context.
    items = partial_analysis(selected)
    # Then the external ancestor does not taint this checkout.
    assert items == ()


@pytest.mark.parametrize(
    "source,expected",
    [
        ('text = "import daytona"\n', False),
        ("# import daytona\nimport requests\n", False),
        ("import requests\n", False),
        ("import os, daytona\n", True),
        ("from daytona import Daytona\n", True),
        ("from daytona_sdk import Daytona\n", True),
    ],
)
def test_requires_import_when_external_package_is_evidence(
    tmp_path: Path, source: str, expected: bool
) -> None:
    # Given an external package with or without a real Daytona import.
    (tmp_path / ".git").mkdir()
    selected = tmp_path / "service"
    selected.mkdir()
    common = tmp_path / "common"
    common.mkdir()
    (common / "__init__.py").write_text(source)
    # When inspecting context omitted from the selected service.
    items = partial_analysis(selected)
    # Then comments and strings do not qualify, but any import alias does.
    assert bool(items) is expected
