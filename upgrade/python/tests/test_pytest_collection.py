# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fixture evidence respects pytest's configured collection rules."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom daytona import Daytona\n"
        "@pytest.fixture\ndef client():\n    return Daytona()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    return tmp_path


@pytest.mark.parametrize("filename", ["app/health.py", "test_app.py", "app_test.py"])
@pytest.mark.parametrize("method", [False, True])
def test_fixture_evidence_when_module_matches_default_collection(
    project: Path, filename: str, method: bool
) -> None:
    # Given a test-named consumer in a production or collected module.
    source = "def test_connection(client):\n    client.get('id')\n"
    if method:
        source = "class TestAPI:\n    def test_connection(self, client):\n        client.get('id')\n"
    consumer = project / filename
    consumer.parent.mkdir(parents=True, exist_ok=True)
    consumer.write_text(source)
    # When planning the migration at the project root.
    result = preflight(project, TransformOptions(load_mapping()))
    # Then only collected modules gain review evidence, never a rewrite.
    assert [item["kind"] for item in result.items] == (
        [] if filename == "app/health.py" else ["fixme"]
    )
    for change in result.changes:
        if change.file.path == consumer:
            assert ast.dump(ast.parse(change.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "config", ["pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"]
)
@pytest.mark.parametrize(
    "setting",
    [
        "python_functions = check_*",
        "python_functions = check",
        "python_files = check_*.py",
        "python_classes = Check",
        "python_classes = *Suite",
    ],
)
def test_fixture_evidence_when_collection_is_configured(
    project: Path, config: str, setting: str
) -> None:
    # Given an override that accepts one consumer and rejects the default name.
    header = "[tool:pytest]" if config == "setup.cfg" else "[pytest]"
    if config == "pyproject.toml":
        header = "[tool.pytest.ini_options]"
        key, value = setting.split(" = ")
        setting = f'{key} = ["{value}"]'
    (project / config).write_text(f"{header}\n{setting}\n")
    if setting.startswith("python_functions"):
        filename = "test_app.py"
        source = (
            "def check_fetch(client):\n    client.get('id')\n"
            "def test_fetch(client):\n    client.get('id')\n"
        )
    elif setting.startswith("python_classes"):
        filename = "test_app.py"
        source = (
            "class CheckSuite:\n    def test_fetch(self, client):\n        client.get('id')\n"
            "class TestAPI:\n    def test_fetch(self, client):\n        client.get('id')\n"
        )
    else:
        filename = "check_app.py"
        source = "def test_fetch(client):\n    client.get('id')\n"
        (project / "test_app.py").write_text(source)
    (project / filename).write_text(source)
    # When planning the complete project with the configured collection rules.
    result = preflight(project, TransformOptions(load_mapping()))
    # Then only the override's consumer needs review and its AST stays intact.
    assert [(item["file"], item["line"]) for item in result.items] == [
        (filename, 3 if setting.startswith("python_classes") else 2)
    ]
    migrated = next(
        change.source for change in result.changes if change.file.relative == filename
    )
    assert ast.dump(ast.parse(migrated)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("decorator", ["@pytest.fixture", "@pytest.fixture()"])
def test_fixture_evidence_when_plugin_module_is_not_collected(
    project: Path, decorator: str
) -> None:
    # Given a decorated fixture in an ordinary plugin module.
    (project / "plugin.py").write_text(
        f"import pytest\n{decorator}\ndef resource(client):\n    client.get('id')\n"
    )
    # When planning the migration.
    result = preflight(project, TransformOptions(load_mapping()))
    # Then fixture dependencies remain eligible outside test modules.
    assert [(item["file"], item["kind"]) for item in result.items] == [
        ("plugin.py", "fixme")
    ]
