# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Docker full-line comments cannot conceal active endpoint settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize("comment", ["# note \\", "  # note \\"])
def test_refuses_custom_url_when_preceding_comment_ends_in_backslash(
    tmp_path: Path, comment: str
) -> None:
    # Given a comment which is not a continued Docker instruction.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_text(
        comment + "\nENV DAYTONA_API_URL https://custom/api\n"
    )
    # When scanning the following real ENV instruction.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the custom endpoint still refuses at its physical line.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("env-api-url", 2),
        ("custom-api-url", 2),
    ]


@pytest.mark.parametrize("comment", ["# note", "  # note \\", "# first\n# second"])
@pytest.mark.parametrize("separator", [" ", "="])
def test_cli_refuses_custom_url_when_comments_interrupt_env_continuation(
    tmp_path: Path, comment: str, separator: str
) -> None:
    # Given a continued custom setting separated by Docker-discarded comments.
    dependency = tmp_path / "requirements.txt"
    dependency.write_text("daytona==0.224.0\n")
    dockerfile = tmp_path / "Dockerfile"
    source = f"ENV DAYTONA_API_URL{separator}\\\n{comment}\n https://custom/api\n"
    dockerfile.write_text(source)
    # When the real CLI attempts the migration without a Cloud assertion.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then refusal preserves both deployment and dependency files.
    assert status == 2
    assert dependency.read_text() == "daytona==0.224.0\n"
    assert dockerfile.read_text() == source


def test_reports_cloud_when_comment_interrupts_split_url(tmp_path: Path) -> None:
    # Given a Cloud hostname split across a continuation with a full-line comment.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "Dockerfile").write_text(
        "ENV DAYTONA_API_URL https://app.daytona.\\\n# discarded\nio/api\n"
    )
    # When logical Docker lines are assembled before URL classification.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then comments disappear without injecting whitespace into the URL.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("env-api-url", 1)
    ]
