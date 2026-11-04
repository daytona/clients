# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose provenance distinguishes CI includes from deployment loaders."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "case",
    [
        (filename, include)
        for filename in [".gitlab-ci.yml", "pipeline.yml"]
        for include in [
            "[{template: Jobs/Test.gitlab-ci.yml}]",
            "\n  - local: /ci/test.yml",
            "\n  - project: team/ci\n    file: test.yml",
            "\n  - remote: https://example.com/ci.yml",
        ]
    ],
)
def test_passes_strict_when_yaml_contains_only_ci_includes(
    tmp_path: Path,
    case: tuple[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given CI loader keys, not Compose include paths.
    filename, include = case
    (tmp_path / filename).write_text(f"include: {include}\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    # When strict migration classifies the YAML by its actual loader contract.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then unrelated includes do not block migration or add manual review.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert "Daytona().v0.create()" in (tmp_path / "app.py").read_text()


@pytest.mark.parametrize("selection", ["COMPOSE_FILE", "include"])
def test_refuses_when_gitlab_filename_is_explicitly_selected_as_compose(
    tmp_path: Path,
    selection: str,
) -> None:
    # Given explicit Compose provenance overriding the GitLab basename.
    files = {
        ".gitlab-ci.yml": "services: {app: {environment: {DAYTONA_API_URL: https://custom/api}}}\n",
        "app.py": "from daytona import Daytona\nDaytona().create()\n",
        "requirements.txt": "daytona==0.224.0\n",
    }
    if selection == "COMPOSE_FILE":
        files[".env"] = "COMPOSE_FILE=.gitlab-ci.yml\n"
    else:
        files["compose.yaml"] = "include: [.gitlab-ci.yml]\n"
    for name, source in files.items():
        (tmp_path / name).write_text(source)
    # When selected manifests receive Compose semantics regardless of basename.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then the selected custom endpoint refuses before every write.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


def test_passes_when_unselected_gitlab_has_a_services_key(tmp_path: Path) -> None:
    # Given GitLab service metadata which is not a Compose deployment.
    (tmp_path / ".gitlab-ci.yml").write_text(
        "services: [{name: postgres}]\ninclude: [{template: test}]\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration inspects the checkout.
    status = main([str(tmp_path), "--force", "--yes", "--strict"])
    # Then neither GitLab top-level key confers Compose provenance.
    assert status == 0
