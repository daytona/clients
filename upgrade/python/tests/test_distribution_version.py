# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Published wheels report their stamped distribution version on both CLI surfaces."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def stamped_cli(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build and install a release-shaped wheel outside the source checkout."""
    scratch = tmp_path_factory.mktemp("stamped-wheel")
    package = Path(__file__).parents[1]
    source = scratch / "package"
    source.mkdir()
    shutil.copytree(
        package / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copyfile(package / "README.md", source / "README.md")
    (source / "pyproject.toml").write_text(
        (package / "pyproject.toml")
        .read_text()
        .replace('version = "0.0.0"', 'version = "1.2.3rc1"', 1)
    )
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(scratch / "dist"), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    environment = scratch / "installed"
    subprocess.run(
        [
            "uv",
            "venv",
            "--python",
            sys.executable,
            str(environment),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next((scratch / "dist").glob("*.whl"))
    scripts = environment / ("Scripts" if sys.platform == "win32" else "bin")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(scripts / ("python.exe" if sys.platform == "win32" else "python")),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return scripts / (
        "daytona-upgrade.exe" if sys.platform == "win32" else "daytona-upgrade"
    )


def test_reports_release_version_when_installed_wheel(
    stamped_cli: Path, tmp_path: Path
) -> None:
    # Given the wheel stamped as a release instead of the source placeholder.
    # When its installed console script prints its version.
    result = subprocess.run(
        [str(stamped_cli), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # Then support sees the published release version.
    assert result.stdout.strip() == "1.2.3rc1"


def test_reports_release_version_when_json_requested(
    stamped_cli: Path, tmp_path: Path
) -> None:
    # Given a customer project inspected by the installed release wheel.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    # When the console script emits its machine-readable report.
    result = subprocess.run(
        [str(stamped_cli), str(tmp_path), "--json", "--dry"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # Then the report carries the release version too, and the release writes
    # exactly the SDK release of its own version (lockstep publishing).
    report = json.loads(result.stdout)
    assert report["version"] == "1.2.3rc1"
    assert report["dependencies"][0]["to"] == "daytona[v0-sdk]==1.2.3rc1"


def test_uses_placeholder_when_distribution_is_not_installed(tmp_path: Path) -> None:
    # Given the source module loaded without site-packages or installed metadata.
    module = Path(__file__).parents[1] / "src/daytona_upgrade/__init__.py"
    # When an isolated Python process loads the source checkout.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            f"import runpy; print(runpy.run_path({str(module)!r})['__version__'])",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # Then only this genuinely uninstalled case uses the placeholder.
    assert result.stdout.strip() == "0.0.0"
