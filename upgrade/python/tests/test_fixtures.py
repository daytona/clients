# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mini-project fixtures exercise real CLI writes and byte-preserving output."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

import pytest
from test_suggestions import suggestion_mapping

from daytona_upgrade import cli

FIXTURES: Final = Path(__file__).parent / "fixtures"
SCENARIOS: Final = (
    "named-imports",
    "aliases",
    "type-only-imports",
    "namespace-import",
    "self-attribute",
    "annotated-param",
    "unannotated-param-fixme",
    "service-chain",
    "context-manager",
    "config-fields",
    "custom-api-url-stop",
    "compose-custom-api-url-stop",
    "cloud-api-url-dropped",
    "old-package-names",
    "deep-import",
    "mock-target",
    "idempotent",
    "cross-module-client",
    "notebook",
    "suggest-v2-basic",
    "suggest-v2-workflow-header",
    "suggest-v2-workflow-client-arguments",
    "suggest-v2-rerun",
    "remove-suggestions",
    "other-ecosystem",
)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_project_when_cli_applies_scenario(
    scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a complete mini project and independently authored expected bytes.
    fixture = FIXTURES / scenario
    project = tmp_path / "project"
    shutil.copytree(fixture / "input", project)
    args = [str(project), "--force", "--yes"]
    if scenario.startswith("suggest-v2"):
        args.append("--suggest-v2")
        monkeypatch.setattr(cli, "load_mapping", suggestion_mapping)
    if scenario == "remove-suggestions":
        args.append("--remove-suggestions")
    # When driving the public CLI rather than calling the transformer directly.
    status = cli.main(args)
    # Then every project file, including manifests, matches its expected bytes.
    assert status == (2 if scenario.endswith("custom-api-url-stop") else 0)
    expected = fixture / "expected"
    actual_paths = sorted(
        path.relative_to(project) for path in project.rglob("*") if path.is_file()
    )
    expected_paths = sorted(
        path.relative_to(expected) for path in expected.rglob("*") if path.is_file()
    )
    assert actual_paths == expected_paths
    for relative in expected_paths:
        assert (project / relative).read_bytes() == (
            expected / relative
        ).read_bytes(), str(relative)


@pytest.mark.parametrize(
    "scenario",
    ["named-imports", "unannotated-param-fixme", "idempotent", "cross-module-client"],
)
def test_upgrade_when_project_is_run_twice(scenario: str, tmp_path: Path) -> None:
    # Given a project upgraded once through the real CLI.
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / scenario / "input", project)
    assert cli.main([str(project), "--force", "--yes"]) == 0
    before = {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    # When applying the same upgrade a second time.
    status = cli.main([str(project), "--force", "--yes"])
    # Then code, imports, manifests and FIXME comments are byte-idempotent.
    assert status == 0
    assert {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    } == before


def test_mapping_when_canonical_source_is_available() -> None:
    # Given the canonical mapping in a source checkout (not an installed wheel).
    canonical = Path(__file__).parents[2] / "mapping" / "mapping.json"
    if not canonical.exists():
        pytest.skip("Canonical mapping is absent outside the upgrade source checkout")
    bundled = Path(cli.__file__).with_name("mapping.json")
    # When comparing the published copy with the single source of truth.
    actual = bundled.read_bytes()
    # Then a stale package copy cannot silently ship.
    assert actual == canonical.read_bytes()
