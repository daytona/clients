# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Independent before/after manifest examples and dependency boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
import tomlkit
from tomlkit.items import InlineTable, Table

from daytona_upgrade.manifests import next_step, rewrite_manifest
from daytona_upgrade.models import load_mapping

_FIXTURES: Final = Path(__file__).parent / "fixtures"
_SCENARIOS: Final = (
    ("requirements", "requirements-dev.txt"),
    ("pep621", "pyproject.toml"),
    ("poetry", "pyproject.toml"),
    ("setup-cfg", "setup.cfg"),
    ("setup-py", "setup.py"),
    ("pipfile", "Pipfile"),
    ("dependency-groups", "pyproject.toml"),
)


@pytest.mark.parametrize(("scenario", "name"), _SCENARIOS)
def test_rewrites_manifest_when_supported_format(scenario: str, name: str) -> None:
    # Given
    folder = _FIXTURES / scenario
    source = (folder / "input" / name).read_text()
    expected = (folder / "expected" / name).read_text()
    # When
    result, changes = rewrite_manifest(Path(name), source, load_mapping())
    # Then
    assert result == expected
    assert changes


@pytest.mark.parametrize(("scenario", "name"), _SCENARIOS)
def test_preserves_manifest_when_already_migrated(scenario: str, name: str) -> None:
    # Given
    source = (_FIXTURES / scenario / "expected" / name).read_text()
    # When
    result, changes = rewrite_manifest(Path(name), source, load_mapping())
    # Then
    assert (result, changes) == (source, [])


@pytest.mark.parametrize(
    "source",
    [
        "daytona-upgrade>=1\n",
        "daytona-tools==1\n",
        "daytona is a client\n",
        "# daytona==0.224\n",
        "--extra-index-url https://daytona.test/simple\n",
        "git+https://github.com/daytona/sdk.git\n",
    ],
)
def test_preserves_line_when_not_daytona_requirement(source: str) -> None:
    # Given
    path = Path("requirements.txt")
    # When
    result = rewrite_manifest(path, source, load_mapping())
    # Then
    assert result == (source, [])


@pytest.mark.parametrize(
    "name", ["requirements.txt", "requirements-extra.txt", "constraints.in"]
)
def test_reports_exact_requirement_change_when_crlf_input(name: str) -> None:
    # Given
    source = '  Daytona_SDK==0.224 ; os_name == "nt"  # windows\r\n'
    expected = '  daytona[v0-sdk]>=2,<3 ; os_name == "nt"  # windows\r\n'
    # When
    result, changes = rewrite_manifest(Path(name), source, load_mapping())
    # Then
    assert result == expected
    assert changes == [(source.strip(), expected.strip())]


def test_preserves_trailing_whitespace_when_requirement_has_no_newline() -> None:
    # Given
    source = "daytona==0.224  "
    # When
    result, changes = rewrite_manifest(Path("requirements.txt"), source, load_mapping())
    # Then
    assert result == "daytona[v0-sdk]>=2,<3  "
    assert changes == [("daytona==0.224", "daytona[v0-sdk]>=2,<3")]


def test_rewrites_inline_option_when_setup_cfg_install_requires() -> None:
    # Given
    source = (
        "[options]\ninstall_requires = daytona-sdk==0.224 # client\nzip_safe = false\n"
    )
    # When
    result, _ = rewrite_manifest(Path("setup.cfg"), source, load_mapping())
    # Then
    assert (
        result
        == "[options]\ninstall_requires = daytona[v0-sdk]>=2,<3 # client\nzip_safe = false\n"
    )


def test_preserves_source_when_unrecognized_filename() -> None:
    # Given
    source = "daytona-sdk==0.224\n"
    # When
    result = rewrite_manifest(Path("notes.txt"), source, load_mapping())
    # Then
    assert result == (source, [])


@pytest.mark.parametrize("prefix", ["", "u", "r"])
def test_preserves_string_prefix_when_setup_dependency_literal(prefix: str) -> None:
    # Given
    source = f"import setuptools as st\nst.setup(install_requires=[{prefix}'daytona-sdk==0.224'])\n"
    # When
    result, _ = rewrite_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert (
        result
        == f"import setuptools as st\nst.setup(install_requires=[{prefix}'daytona[v0-sdk]>=2,<3'])\n"
    )


@pytest.mark.parametrize(
    "expression",
    [
        "['daytona' + '-upgrade>=1']",
        "read_requirements('daytona')",
        "[f'daytona{version}']",
        "['daytona-upgrade>=1']",
    ],
)
def test_preserves_setup_expression_when_not_static_requirement(
    expression: str,
) -> None:
    # Given
    source = f"from setuptools import setup\nsetup(install_requires={expression})\n"
    # When
    result = rewrite_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result == (source, [])


def test_preserves_marker_when_requirement_uses_direct_url() -> None:
    # Given
    source = "daytona-sdk @ https://example.test/sdk.whl ; sys_platform == 'linux' # mirror\n"
    # When
    result, _ = rewrite_manifest(Path("requirements.txt"), source, load_mapping())
    # Then
    assert result == "daytona[v0-sdk]>=2,<3 ; sys_platform == 'linux' # mirror\n"


def test_replaces_source_when_poetry_dependency_uses_git() -> None:
    # Given
    source = '[tool.poetry.dependencies]\ndaytona = {git = "https://example.test/sdk.git", tag = "v0.224", optional = true, extras = ["otel"]} # client\n'
    # When
    result, _ = rewrite_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then
    dependency = tomlkit.parse(result)["tool"]
    for key in ("poetry", "dependencies", "daytona"):
        assert isinstance(dependency, Table)
        dependency = dependency[key]
    assert isinstance(dependency, InlineTable)
    assert dependency.unwrap() == {
        "version": ">=2,<3",
        "optional": True,
        "extras": ["otel", "v0-sdk"],
    }
    assert result.endswith(" # client\n")


@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [
        ("uv.lock", "uv lock"),
        ("poetry.lock", "poetry lock"),
        ("pdm.lock", "pdm lock"),
        ("Pipfile.lock", "pipenv lock"),
        ("", 'pip install -U "daytona[v0-sdk]==2.0.0a8"'),
    ],
)
def test_selects_next_command_when_lockfile_present(
    tmp_path: Path, lockfile: str, expected: str
) -> None:
    # Given
    if lockfile:
        (tmp_path / lockfile).write_text("untouched lock data")
    # When
    result = next_step(tmp_path, "daytona[v0-sdk]==2.0.0a8")
    # Then
    assert result == expected


def test_prefers_uv_when_multiple_lockfiles_present(tmp_path: Path) -> None:
    # Given
    for lockfile in ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock"):
        (tmp_path / lockfile).touch()
    # When
    result = next_step(tmp_path, "daytona[v0-sdk]==2.0.0a8")
    # Then
    assert result == "uv lock"
