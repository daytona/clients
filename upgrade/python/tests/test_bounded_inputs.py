# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Large YAML and special deployment inputs remain bounded through the CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from daytona_upgrade.regular_files import INPUT_LIMIT, read_regular_bytes


@pytest.fixture(autouse=True)
def dependency(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")


def run_cli(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "daytona_upgrade",
            str(root),
            "--force",
            "--dry",
            "--strict",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        # A hang guard; the release build raises it (DAYTONA_UPGRADE_SKIP_WALL_CLOCK),
        # like the npm tool's testTimeout(), so a slow runner blocks no release.
        timeout=300 if os.environ.get("DAYTONA_UPGRADE_SKIP_WALL_CLOCK") == "1" else 8,
    )


@pytest.mark.parametrize("compose", [False, True])
def test_finishes_within_budget_when_yaml_is_large(
    tmp_path: Path, compose: bool
) -> None:
    # Given a 56 MiB generated schema containing nested services/include text.
    source = tmp_path / "openapi.yaml"
    with source.open("w") as stream:
        stream.write("services: {}\n" if compose else "openapi: 3.0.0\n")
        block = "  schema: {services: data, include: data}\n" * 1024
        for _ in range(1400):
            stream.write(block)
    # When the real CLI has a bounded wall-time budget for discovery.
    result = run_cli(tmp_path)
    # Then unrelated YAML is ignored; oversized Compose retains existing review.
    report = json.loads(result.stdout)
    assert result.returncode == (1 if compose else 0), result.stderr
    assert [item["kind"] for item in report["items"]] == (
        ["env-api-url"] if compose else []
    )


@pytest.mark.skipif(sys.platform == "win32", reason="Requires POSIX FIFOs")
@pytest.mark.parametrize(
    "loader",
    ["discovery", "compose-include", "compose-env", "dotenv", "compose-selection"],
)
def test_finishes_when_deployment_input_is_a_fifo(tmp_path: Path, loader: str) -> None:
    # Given a pipe with no writer, reached through each deployment read seam.
    filename = "pipe.yaml" if loader == "discovery" else "runtime.conf"
    os.mkfifo(tmp_path / filename)
    if loader == "compose-include":
        (tmp_path / "compose.yaml").write_text(f"include: [{filename}]\n")
    elif loader == "compose-env":
        (tmp_path / "compose.yaml").write_text(
            f"services: {{app: {{env_file: {filename}}}}}\n"
        )
    elif loader == "dotenv":
        (tmp_path / "app.py").write_text(
            f"from dotenv import load_dotenv\nload_dotenv('{filename}')\n"
        )
    elif loader == "compose-selection":
        (tmp_path / ".env").write_text(f"COMPOSE_FILE={filename}\n")
    # When no path is allowed to open a blocking special file.
    result = run_cli(tmp_path)
    # Then unverifiable inputs report review rather than hanging or passing.
    report = json.loads(result.stdout)
    assert result.returncode == 1, result.stderr
    assert any(
        item["kind"] in {"env-api-url", "unsupported-file"} for item in report["items"]
    )


@pytest.mark.skipif(not Path("/dev/zero").exists(), reason="Requires /dev/zero")
def test_rejects_when_input_is_a_device() -> None:
    # Given an infinite byte source rather than a regular project file.
    device = Path("/dev/zero")
    # When the shared reader checks the input before opening it.
    with pytest.raises(OSError) as error:
        read_regular_bytes(device)
    # Then no device bytes are consumed.
    assert error.value.errno == 22


def test_reviews_when_loaded_environment_exceeds_byte_budget(tmp_path: Path) -> None:
    # Given a regular file larger than the supported environment scan budget.
    (tmp_path / "runtime.conf").write_bytes(b"X" * (INPUT_LIMIT + 1))
    (tmp_path / "compose.yaml").write_text(
        "services: {app: {env_file: runtime.conf}}\n"
    )
    # When the CLI cannot certify the loaded environment within its byte cap.
    result = run_cli(tmp_path)
    # Then oversized input remains an existing environment review, not refusal.
    report = json.loads(result.stdout)
    assert result.returncode == 1, result.stderr
    assert [item["kind"] for item in report["items"]] == ["env-api-url"]
