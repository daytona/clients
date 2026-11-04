# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unverified client configurations need actionable source and report guidance."""

from __future__ import annotations

import ast
import json
from difflib import ndiff
from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade.cli import main
from daytona_upgrade.config_messages import SHARED_URL_WRITE, UNRESOLVED_URL_FIELDS


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize(
    ("body", "visible_write"),
    [
        ("from helper import cfg\nCLIENT(cfg)\n", False),
        ("def build(cfg: DaytonaConfig):\n    return CLIENT(cfg)\n", False),
        (
            "def build(cfg: DaytonaConfig):\n    print(cfg)\n    return CLIENT(cfg)\n",
            False,
        ),
        ("cfg = DaytonaConfig()\nsetattr(cfg, 'server_url', url)\nCLIENT(cfg)\n", True),
        ("cfg = DaytonaConfig()\nCLIENT(cfg)\nsetattr(cfg, 'api_url', url)\n", True),
        (
            "cfg = DaytonaConfig()\nalias = cfg\nCLIENT(cfg)\nalias.api_url = url\n",
            True,
        ),
        ("cfg = DaytonaConfig(api_url=None)\ncustomize(cfg)\nCLIENT(cfg)\n", True),
        (
            "other = DaytonaConfig()\nother.api_url = url\ndef build(cfg: DaytonaConfig):\n    return CLIENT(cfg)\n",
            False,
        ),
    ],
)
def test_marks_constructor_when_config_ownership_is_unverified(
    client: str, body: str, visible_write: bool
) -> None:
    # Given an unresolved constructor, with or without a related visible URL write.
    source = (
        "from daytona import Daytona, AsyncDaytona, DaytonaConfig\n"
        + body.replace("CLIENT", client)
    )
    expected = SHARED_URL_WRITE if visible_write else UNRESOLVED_URL_FIELDS

    # When the operator asserts Cloud use for an unresolved configuration.
    result = migrate(source, drop=True)

    # Then executable code survives and its constructor gains the fitting FIXME.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    additions = [
        line[2:].lstrip()
        for line in ndiff(source.splitlines(), result.source.splitlines())
        if line.startswith("+ ")
    ]
    assert "# FIXME(daytona-upgrade): " + expected in additions
    assert all(line.startswith("# FIXME(daytona-upgrade): ") for line in additions)
    assert any(item["kind"] == "config-field" for item in result.items)


@pytest.mark.parametrize(
    "body",
    [
        body.replace("CLIENT", client)
        for client in ["Daytona", "AsyncDaytona"]
        for body in [
            "from helper import cfg\nCLIENT(cfg)\n",
            "def build(cfg: DaytonaConfig):\n    return CLIENT(cfg)\n",
            "cfg = DaytonaConfig()\nsetattr(cfg, 'server_url', url)\nCLIENT(cfg)\n",
            "cfg = DaytonaConfig()\nCLIENT(cfg)\nsetattr(cfg, 'api_url', url)\n",
        ]
    ],
)
def test_strict_fails_when_unverified_constructor_is_asserted_cloud(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    # Given the previously uncommented constructor shapes in a real project.
    source = "from daytona import Daytona, AsyncDaytona, DaytonaConfig\n" + body
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When strict migration accepts the Cloud assertion but still requires review.
    status = main(
        [str(tmp_path), "--force", "--yes", "--drop-api-url", "--strict", "--json"]
    )

    # Then an app-local manual report item makes strict fail rather than pass.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(
        item["kind"] == "config-field" and item["file"] == "app.py"
        for item in report["items"]
    )


@pytest.mark.parametrize("extension", [".py", ".ipynb"])
def test_retains_only_config_review_when_upgrade_adds_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extension: str
) -> None:
    # Given an unresolved constructor but no executable rewrite or dependency input.
    code = "from daytona import Daytona\ndef build(cfg):\n    return Daytona(cfg)\n"
    source = (
        json.dumps({"cells": [{"cell_type": "code", "source": code}]})
        if extension == ".ipynb"
        else code
    )
    (tmp_path / ("app" + extension)).write_text(source)

    # When only a runtime-refusal warning is added to the source.
    status = main([str(tmp_path), "--force", "--yes", "--drop-api-url", "--json"])

    # Then the warning does not masquerade as a new dependency-requiring rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [item["kind"] for item in report["items"]] == ["config-field"]
