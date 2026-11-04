# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Project discovery retains factory and pytest fixture return evidence."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "project",
    [
        pytest.param(
            (
                "def get_client():\n    return Daytona()\n",
                "from provider import get_client\nclient = get_client()\ndef run():\n    return client.create()\n",
            ),
            id="imported-factory",
        ),
        pytest.param(
            (
                "def get_client():\n    return Daytona()\n",
                "import provider\nclient = provider.get_client()\ndef run():\n    return client.create()\n",
            ),
            id="qualified-imported-factory",
        ),
        pytest.param(
            (
                "from functools import cache\n@cache\ndef get_client():\n    return Daytona()\n",
                "from provider import get_client\nclient = get_client()\ndef run():\n    return client.create()\n",
            ),
            id="imported-cached-factory",
        ),
    ],
)
def test_strict_requires_review_when_project_factory_returns_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], project: tuple[str, str]
) -> None:
    # Given a consumer with no Daytona import of its own.
    provider, consumer = project
    provider_source = "from daytona import Daytona\n" + provider
    (tmp_path / "provider.py").write_text(provider_source)
    app = tmp_path / "app.py"
    app.write_text(consumer)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When upgrading the complete project through the real strict CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the factory's consumer is manual, not a silent success or rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["kind"], item["file"]) for item in report["items"]] == [
        ("fixme", "app.py")
    ]
    assert "# FIXME(daytona-upgrade):" in app.read_text()
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(consumer))
    assert (tmp_path / "provider.py").read_text() == provider_source


@pytest.mark.parametrize("statement", ["return Daytona()", "yield Daytona()"])
def test_strict_requires_review_when_conftest_fixture_supplies_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], statement: str
) -> None:
    # Given pytest's implicit injection from a discovered conftest module.
    fixture = (
        "from daytona import Daytona\nimport pytest\n@pytest.fixture\n"
        f"def client():\n    {statement}\n"
    )
    (tmp_path / "conftest.py").write_text(fixture)
    source = "def test_x(client):\n    return client.create()\n"
    app = tmp_path / "test_app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When analyzing all project files rather than explicit import edges alone.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then an implicitly injected client cannot disappear from the strict report.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["kind"], item["file"]) for item in report["items"]] == [
        ("fixme", "test_app.py")
    ]
    assert "# FIXME(daytona-upgrade):" in app.read_text()
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
    assert (tmp_path / "conftest.py").read_text() == fixture
