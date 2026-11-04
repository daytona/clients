# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Subdirectory scans discover the nearest applicable pytest configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.fixture
def subfolder(tmp_path: Path) -> Path:
    folder = tmp_path / "tests"
    folder.mkdir()
    (folder / "conftest.py").write_text(
        "import pytest\nfrom daytona import Daytona\n"
        "@pytest.fixture\ndef client():\n    return Daytona()\n"
    )
    (folder / "requirements.txt").write_text("daytona==0.224.0\n")
    (folder / "test_app.py").write_text(
        "def check_fetch(client):\n    client.get('id')\n"
        "def verify_fetch(client):\n    client.get('id')\n"
        "def test_fetch(client):\n    client.get('id')\n"
    )
    return folder


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("pytest.ini", "[pytest]\npython_functions = check_*\n"),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\npython_functions = ["check_*"]\n',
        ),
        ("tox.ini", "[pytest]\npython_functions = check_*\n"),
        ("setup.cfg", "[tool:pytest]\npython_functions = check_*\n"),
    ],
)
def test_fixture_evidence_when_configuration_is_in_ancestor(
    subfolder: Path, filename: str, source: str
) -> None:
    # Given custom collection configured above the scanned subfolder.
    (subfolder.parent / filename).write_text(source)
    # When planning only that subfolder.
    result = preflight(subfolder, TransformOptions(load_mapping()))
    # Then only the custom-collected function gains fixture-derived evidence.
    assert [(item["file"], item["line"], item["kind"]) for item in result.items] == [
        ("test_app.py", 2, "fixme")
    ]


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("pytest.ini", "[pytest]\npython_functions = verify_*\n"),
        (
            "pyproject.toml",
            '[tool.pytest.ini_options]\npython_functions = ["verify_*"]\n',
        ),
        ("tox.ini", "[pytest]\npython_functions = verify_*\n"),
        ("setup.cfg", "[tool:pytest]\npython_functions = verify_*\n"),
    ],
)
def test_fixture_evidence_when_nearer_configuration_overrides_ancestor(
    subfolder: Path, filename: str, source: str
) -> None:
    # Given an ancestor config and a nearer config with different collection.
    (subfolder.parent / "pytest.ini").write_text(
        "[pytest]\npython_functions = check_*\n"
    )
    (subfolder / filename).write_text(source)
    # When planning the subfolder containing the nearer configuration.
    result = preflight(subfolder, TransformOptions(load_mapping()))
    # Then configuration proximity wins over ancestor filename priority.
    assert [(item["file"], item["line"], item["kind"]) for item in result.items] == [
        ("test_app.py", 4, "fixme")
    ]


@pytest.mark.parametrize("source", ['[project]\nname = "example"\n', "[tool.pytest]\n"])
def test_fixture_evidence_when_nearer_pyproject_has_no_pytest_options(
    subfolder: Path, source: str
) -> None:
    # Given a nearer TOML file without the applicable pytest section.
    (subfolder.parent / "pytest.ini").write_text(
        "[pytest]\npython_functions = check_*\n"
    )
    (subfolder / "pyproject.toml").write_text(source)
    # When planning the subfolder.
    result = preflight(subfolder, TransformOptions(load_mapping()))
    # Then the unrelated TOML file does not mask ancestor collection settings.
    assert [(item["file"], item["line"], item["kind"]) for item in result.items] == [
        ("test_app.py", 2, "fixme")
    ]
