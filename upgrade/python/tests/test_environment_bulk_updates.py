# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Environment update forms use the same refusal and override contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "arguments",
    [
        "[('DAYTONA_API_URL', 'https://self.host/api')]",
        "**{'DAYTONA_API_URL': 'https://self.host/api'}",
        "{'OTHER': 'ok', **{'DAYTONA_SERVER_URL': 'https://self.host/api'}}",
    ],
)
@pytest.mark.parametrize("drop_api_url", [False, True])
def test_environment_update_is_reported_when_mapping_contains_endpoint(
    tmp_path: Path, arguments: str, drop_api_url: bool
) -> None:
    # Given a real os.environ update containing a known self-hosted endpoint.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(f"import os\nos.environ.update({arguments})\n")
    # When preflighting with the caller's explicit override choice.
    result = preflight(
        tmp_path, TransformOptions(load_mapping(), drop_api_url=drop_api_url)
    )
    # Then refusal changes only with the override; manual review always survives.
    assert [item["kind"] for item in result.items] == (
        ["env-api-url"] if drop_api_url else ["env-api-url", "custom-api-url"]
    )


def test_environment_update_requires_review_when_mapping_is_dynamic(
    tmp_path: Path,
) -> None:
    # Given an update whose keys cannot be proven statically.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text("import os\nos.environ.update(settings)\n")
    # When preflighting the unknown mapping.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then it remains a manual item, not a silently missed write or invented URL.
    assert [item["kind"] for item in result.items] == ["env-api-url"]
