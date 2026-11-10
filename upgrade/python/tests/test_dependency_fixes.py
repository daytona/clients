# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression drivers for call-site bindings, collisions and include modes."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from tomlkit.items import AbstractTable

from daytona_upgrade._requirements import requirement_paths
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


def test_rewrites_used_binding_when_setup_name_is_reassigned_later() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n"
        "setup(install_requires=deps)\n"
        "deps = ['daytona-sdk==0.225']\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )


def test_rewrites_annotation_when_setup_binding_has_value() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps: list[str] = ['daytona-sdk==0.224']\n"
        "setup(install_requires=deps)\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )


@pytest.mark.parametrize(
    "filename,section",
    [
        ("pyproject.toml", "tool.poetry.dependencies"),
        ("Pipfile", "packages"),
    ],
)
@pytest.mark.parametrize(
    "entries",
    [
        "daytona-sdk = {version='0.224',optional=true}\ndaytona = {version='0.225',extras=['otel']}\n",
        "daytona = {version='0.225',extras=['otel']}\ndaytona-sdk = {version='0.224',optional=true}\n",
        "Daytona_SDK = '0.224'\ndaytona-sdk = '0.225'\n",
    ],
)
def test_preserves_declarations_when_package_names_collide(
    filename: str, section: str, entries: str
) -> None:
    # Given
    source = f"[{section}]\n{entries}"
    # When
    result = inspect_manifest(Path(filename), source, load_mapping())
    # Then
    assert result.source == source
    assert result.found
    assert not result.changes
    assert result.manual


def test_retains_constraint_mode_when_constraint_includes_requirement(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "requirements.txt"
    root.write_text("-c constraints\n")
    (tmp_path / "constraints").write_text("-r nested\n")
    nested = tmp_path / "nested"
    nested.write_text("daytona==0.224\n")
    # When
    result = requirement_paths(tmp_path, [root])
    # Then
    assert nested in result.constraints


def test_migrates_poetry_when_only_old_supported_tomlkit_api_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Table.item was introduced after the declared minimum tomlkit version.
    monkeypatch.delattr(AbstractTable, "item")
    source = "[tool.poetry.dependencies]\ndaytona-sdk = '0.224'\n"
    # When
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then
    assert tomlkit.parse(result.source).unwrap() == {
        "tool": {
            "poetry": {
                "dependencies": {"daytona": {"version": ">=2,<3", "extras": ["v0-sdk"]}}
            }
        }
    }


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (
            "pyproject.toml",
            "[tool.poetry.dependencies]\n"
            'daytona-sdk = {version=">=0.100", extras=["x", "otel"]}\n',
            "[tool.poetry.dependencies]\n"
            'daytona = {version=">=2,<3", extras=["otel", "v0-sdk"]}\n',
        ),
        (
            "requirements.txt",
            "daytona-sdk[x,otel]>=0.100\n",
            "daytona[otel,v0-sdk]>=2,<3\n",
        ),
    ],
)
def test_drops_extras_the_renamed_package_does_not_define(
    path: str, source: str, expected: str
) -> None:
    # Given a 0.x declaration with an extra SDK 2.x does not define and one it does.
    # When
    result = inspect_manifest(Path(path), source, load_mapping())
    # Then the known extra is kept, the unknown one dropped and reported.
    assert result.source == expected
    assert [message for _, message in result.manual] == [
        "Dropped the x extra of daytona-sdk: SDK 2.x's daytona has no such extra "
        "(its extras: otel, v0-sdk); install what it provided directly if you "
        "still need it."
    ]


def test_known_extras_match_the_sdk_distribution() -> None:
    # Given the 2.x SDK's own optional dependencies (minus its development extra).
    pyproject = Path(__file__).parents[3] / "sdk-python" / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("not in the repository checkout")
    extras = tomlkit.parse(pyproject.read_text())["project"]["optional-dependencies"]
    # Then the mapping lists exactly those.
    assert sorted(load_mapping().known_extras) == sorted(
        name for name in extras if name != "dev"
    )


def test_reports_unknown_extras_of_a_hash_locked_requirement() -> None:
    # Given an already-2.x hash-locked declaration with an unknown extra.
    source = "daytona[x,v0-sdk]>=2,<3 --hash=sha256:" + "0" * 64 + "\n"
    # When
    result = inspect_manifest(Path("requirements.txt"), source, load_mapping())
    # Then the record is preserved and the extra is reported for manual removal.
    assert result.source == source
    assert [message for _, message in result.manual] == [
        "Remove the x extra of daytona manually: SDK 2.x's daytona has no such "
        "extra (its extras: otel, v0-sdk); the hash-locked or continued record "
        "was preserved."
    ]


def test_does_not_duplicate_an_extra_spelled_differently() -> None:
    # Given
    source = "daytona-sdk[V0_SDK]>=0.100\n"
    # When
    result = inspect_manifest(Path("requirements.txt"), source, load_mapping())
    # Then
    assert result.source == "daytona[V0_SDK]>=2,<3\n"


def test_withdraws_dropped_extra_items_when_the_manifest_is_preserved() -> None:
    # Given a Poetry project whose extras table cannot follow the rename.
    source = (
        "[tool.poetry.dependencies]\n"
        'daytona-sdk = {version = ">=0.100", extras = ["x"]}\n'
        "[tool.poetry]\n"
        'extras = "unreadable"\n'
    )
    # When
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then nothing reports an edit that never reached the file.
    assert result.source == source
    assert not any("Dropped the x extra" in message for _, message in result.manual)


def test_poetry_extras_are_not_duplicated_by_spelling() -> None:
    # Given
    source = (
        "[tool.poetry.dependencies]\n"
        'daytona-sdk = {version = ">=0.100", extras = ["V0_SDK"]}\n'
    )
    # When
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then
    assert 'extras = ["V0_SDK"]' in result.source
