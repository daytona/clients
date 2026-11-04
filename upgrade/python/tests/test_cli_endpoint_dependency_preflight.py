# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The CLI checks endpoints and dependencies when Git inspection is bypassed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


def test_cli_refuses_when_tuple_target_sets_custom_endpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given migratable source with an environment alias created by the first target.
    source = (
        "import os\nfrom daytona import Daytona\n"
        "env, env['DAYTONA_API_URL'] = os.environ, 'https://self/api'\n"
        "Daytona().create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    dependency = tmp_path / "requirements.txt"
    dependency.write_text("daytona==0.224.0\n")
    # When the writing CLI runs without an endpoint override.
    status = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then refusal is transactional and exposes both endpoint diagnostics.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert [item["kind"] for item in report["items"]] == [
        "env-api-url",
        "custom-api-url",
    ]
    assert app.read_text() == source
    assert dependency.read_text() == "daytona==0.224.0\n"


def test_cli_strict_is_manual_when_parent_only_declares_optional_runtime(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an old parent runtime plus a modern testing-only extra.
    (tmp_path / ".git").mkdir()
    source = (
        "from setuptools import setup\n"
        "setup(install_requires=['daytona==0.224.0'], "
        "extras_require={'tests': ['daytona[v0-sdk]>=2']})\n"
    )
    manifest = tmp_path / "setup.py"
    manifest.write_text(source)
    root = tmp_path / "src"
    root.mkdir()
    (root / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating the subtree in strict mode.
    status = main([str(root), "--force", "--yes", "--json", "--strict"])
    # Then the CLI signals manual dependency work and never changes the parent.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "manual-dependency" for item in report["items"])
    assert manifest.read_text() == source


def test_cli_migrates_when_docker_only_echoes_an_endpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given documentation text in RUN rather than an ENV/ARG declaration.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    dockerfile = tmp_path / "Dockerfile"
    source = 'RUN echo "DAYTONA_API_URL=https://self/api"\n'
    dockerfile.write_text(source)
    # When upgrading through the strict writing CLI.
    status = main([str(tmp_path), "--force", "--yes", "--json", "--strict"])
    # Then irrelevant text does not block the actual source migration.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert "Daytona().v0.create()" in app.read_text()
    assert dockerfile.read_text() == source


def test_cli_json_suggests_root_when_receiver_is_a_v0_alias(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a stored v0 alias and its live root owner.
    app = tmp_path / "app.py"
    source = (
        "from daytona import Daytona\napi = Daytona()\n"
        "legacy = api.v0\nlegacy.create()\n"
    )
    app.write_text(source)
    # When requesting read-only JSON suggestions from the real entrypoint.
    status = main([str(tmp_path), "--force", "--suggest-v2", "--json"])
    # Then the suggested code has a valid root receiver and writes nothing.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [
        item["suggestion"]["v2"] for item in report["items"] if "suggestion" in item
    ] == ["api.v2.create(image, region=region)"]
    assert app.read_text() == source
