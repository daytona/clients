# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose provenance survives YAML presentation, not unrelated CI aliases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "case",
    [
        (provenance, include)
        for provenance in ["none", "filename", "selection", "services"]
        for include in ["*shared", "[*shared]"]
    ],
)
def test_reviews_alias_only_when_compose_provenance_exists(
    tmp_path: Path, case: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Given identical unresolved includes with independently established provenance.
    provenance, include = case
    filename = "compose.yml" if provenance == "filename" else "pipeline.yml"
    source = f"shared: &shared [ci.yml]\ninclude: {include}\n"
    if provenance == "services":
        source += "services: {}\n"
    (tmp_path / filename).write_text(source)
    if provenance == "selection":
        (tmp_path / ".env").write_text(f"COMPOSE_FILE={filename}\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration classifies the include by its deployment contract.
    status = main([str(tmp_path), "--force", "--dry", "--strict", "--json"])
    # Then aliases alone are inert, while known Compose inputs retain review.
    report = json.loads(capsys.readouterr().out)
    assert status == (0 if provenance == "none" else 1)
    assert bool(report["items"]) == (provenance != "none")
    assert all(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize(
    "prefix",
    ["# comment with {braces}\n", "\n\n", "---\n", "# comment\n\n--- # document\n"],
)
@pytest.mark.parametrize(
    "setting",
    ["env_file: runtime.conf", "environment: {DAYTONA_API_URL: https://custom/api}"],
)
def test_refuses_custom_endpoint_when_root_flow_has_a_preamble(
    tmp_path: Path, prefix: str, setting: str
) -> None:
    # Given a custom-named root flow mapping after ordinary YAML preamble lines.
    (tmp_path / "pipeline.yml").write_text(
        prefix + "{services: {app: {" + setting + "}}}\n"
    )
    (tmp_path / "runtime.conf").write_text("DAYTONA_API_URL=https://custom/api\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migration follows both inline settings and loaded environments.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then custom endpoints refuse migration rather than escaping discovery.
    assert status == 2
    assert (tmp_path / "requirements.txt").read_text() == "daytona==0.224.0\n"


@pytest.mark.parametrize("key", ["'services'", '"services"'])
def test_refuses_custom_endpoint_when_root_block_key_is_quoted(
    tmp_path: Path, key: str
) -> None:
    # Given a quoted services key in a custom-named block manifest.
    (tmp_path / "pipeline.yml").write_text(
        f"{key}:\n  app:\n    env_file: runtime.conf\n"
    )
    (tmp_path / "runtime.conf").write_text("DAYTONA_API_URL=https://custom/api\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migration recognizes the decoded root key.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then loader settings still participate in refusal.
    assert status == 2


@pytest.mark.parametrize(
    "include",
    [
        "[clean.conf]",
        "[{path: clean.conf, env_file: *shared}]",
        "[{project_directory: .}]",
    ],
)
def test_recognizes_compose_when_include_has_literal_paths_or_compose_fields(
    tmp_path: Path, include: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given explicit Compose include syntax without a recognized filename.
    (tmp_path / "pipeline.yml").write_text(f"include: {include}\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict preflight encounters missing or uncertain include inputs.
    status = main([str(tmp_path), "--force", "--dry", "--strict", "--json"])
    # Then positive include provenance still exposes unresolved inputs.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


def test_reviews_include_when_root_flow_follows_document_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an include-only Compose mapping following a document marker.
    (tmp_path / "pipeline.yml").write_text(
        "# comment\n---\n{include: [missing.conf]}\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When discovery and include parsing agree on the root collection.
    status = main([str(tmp_path), "--force", "--dry", "--strict", "--json"])
    # Then missing included manifests cannot silently disappear.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])
