# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency, namespace, and deployment endpoints retain migration boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_flow import migrate
from test_service_suggestion_ownership import suggest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "manifest,source",
    [
        ("requirements.txt", "daytona>=2\n"),
        ("pyproject.toml", '[project]\ndependencies = ["daytona>=2"]\n'),
        ("pyproject.toml", '[tool.poetry.dependencies]\ndaytona = "^2"\n'),
        ("Pipfile", '[packages]\ndaytona = {version = ">=2"}\n'),
        ("setup.cfg", "[options]\ninstall_requires =\n    daytona>=2\n"),
        (
            "setup.py",
            'from setuptools import setup\nsetup(install_requires=["daytona>=2"])\n',
        ),
    ],
)
def test_manual_dependency_when_parent_omits_v0_extra(
    tmp_path: Path, manifest: str, source: str
) -> None:
    # Given a read-only modern parent without the dependencies required by .v0.
    (tmp_path / ".git").mkdir()
    declaration = tmp_path / manifest
    declaration.write_text(source)
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When upgrading only the child source directory.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then .v0's missing extra is actionable without modifying the parent.
    assert [item["kind"] for item in result.items] == [
        "manual-dependency",
        "types-unresolved",
    ]
    assert declaration.read_text() == source


@pytest.mark.parametrize("name", ["Daytona", "DAYTONA", "daytona"])
def test_parent_satisfies_dependency_when_poetry_name_case_differs(
    tmp_path: Path, name: str
) -> None:
    # Given a modern parent with its compatibility dependencies enabled.
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.poetry.dependencies]\n{name} = {{version = "^2", extras = ["v0-sdk"]}}\n'
    )
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When package recognition inspects the parent without rewriting it.
    result = preflight(root, TransformOptions(load_mapping()))
    # Then package identity is case-insensitive.
    assert [item["kind"] for item in result.items] == ["types-unresolved"]


@pytest.mark.parametrize(
    "url", ["https://app.daytona.io/api//", "HTTPS://APP.DAYTONA.IO/api//"]
)
def test_refuses_config_when_cloud_path_has_multiple_trailing_slashes(
    tmp_path: Path, url: str
) -> None:
    # Given a non-Cloud path which cannot be normalized by removing one slash.
    source = (
        f"from daytona import DaytonaConfig\ncfg = DaytonaConfig(api_url={url!r})\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    # When the real CLI checks config even with a Cloud environment assertion.
    status = main([str(tmp_path), "--force", "--yes", "--drop-api-url"])
    # Then no custom config value is silently removed.
    assert status == 2
    assert app.read_text() == source


@pytest.mark.parametrize(
    "assignment,call",
    [
        ("legacy = api.v0", "legacy.create()"),
        ("legacy = api.v0\nother = legacy", "other.create()"),
        ("legacy = api.v0", "legacy.volume.list()"),
        ("legacy = api.v0\nclients = [legacy]", "clients[0].create()"),
    ],
)
def test_keeps_namespace_when_v0_client_is_aliased(assignment: str, call: str) -> None:
    # Given code which already selects the compatibility client.
    source = (
        "from daytona import Daytona\napi = Daytona()\n"
        + assignment
        + "\n"
        + call
        + "\n"
    )
    # When migrating the source.
    result = migrate(source)
    # Then neither direct aliases nor their derived services gain another .v0.
    assert result.source == source
    assert result.items == ()


def test_keeps_suggestion_when_v0_client_is_aliased() -> None:
    # Given a compatibility client alias which produces a sandbox.
    source = "from daytona import Daytona\napi = Daytona()\nlegacy = api.v0\nsb = legacy.create()\nsb.set_labels({})\n"
    # When requesting v2 guidance on that existing v0 workflow.
    result = suggest(source)
    # Then namespace tracking preserves client and sandbox suggestions.
    assert [
        item["suggestion"]["id"]
        for item in result.items
        if "suggestion" in item and item["kind"] != "workflow"
    ] == [
        "client.create",
        "sandbox.setLabels",
    ]


@pytest.mark.parametrize("separator", [" ", "="])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_reports_cloud_when_docker_env_value_is_continued(
    tmp_path: Path, separator: str, ending: str
) -> None:
    # Given a Cloud ENV value continued onto the next Dockerfile line.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_bytes(
        (
            f"FROM python:3.12{ending}ENV DAYTONA_API_URL{separator}\\{ending}"
            f"    HTTPS://APP.DAYTONA.IO/api/{ending}"
        ).encode()
    )
    # When scanning deployment settings.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the joined Cloud URL is review-only, with its original line location.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("env-api-url", 2)
    ]
