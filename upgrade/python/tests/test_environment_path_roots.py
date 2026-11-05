# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Environment classification is relative to the checkout; outside Git the whole path counts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("git", [False, True])
def test_deployment_ancestor_counts_only_outside_a_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], git: bool
) -> None:
    # Given a non-deployment values file under a deploy-named checkout parent.
    root = tmp_path / "deploy/app"
    root.mkdir(parents=True)
    (root / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    (root / "values.yaml").write_text(
        "DAYTONA_API_URL: https://daytona.internal.corp/api\n"
    )
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    # When
    status = main([str(root), "--dry", "--json"])
    # Then a checkout classifies from its own root; outside Git the whole path counts.
    items = json.loads(capsys.readouterr().out)["items"]
    assert status == (0 if git else 2)
    assert any(item["kind"] == "custom-api-url" for item in items) is not git


@pytest.mark.parametrize("git", [False, True])
@pytest.mark.parametrize("scan", [".", "deploy", "deploy/values.yaml"])
def test_refuses_deployment_url_when_path_is_relative_to_available_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], git: bool, scan: str
) -> None:
    # Given a deployment in a repository, or an otherwise identical plain tree.
    root = tmp_path / "app"
    deployment = root / "deploy"
    deployment.mkdir(parents=True)
    (deployment / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    (deployment / "values.yaml").write_text(
        "DAYTONA_API_URL: https://daytona.internal.corp/api\n"
    )
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    # When
    status = main([str(root / scan), "--dry", "--json"])
    # Then narrowing the scan root never hides the deployment file.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
