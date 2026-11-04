# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unknown environment names cannot be reported as a proven API variable."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "write",
    [
        "os.environ.update(settings)",
        "os.environ[key] = value",
        "os.putenv(key, value)",
        "os.environ.setdefault(key, value)",
    ],
)
def test_generic_review_when_environment_name_is_unresolved(
    tmp_path: Path, write: str
) -> None:
    # Given an environment write that may affect either API variable.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "app.py").write_text("import os\n" + write + "\n")
    # When reporting the source write.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then no particular variable is claimed to have been proven.
    assert [(item["kind"], item["message"]) for item in result.items] == [
        (
            "env-api-url",
            "an environment write may set DAYTONA_API_URL or DAYTONA_SERVER_URL; "
            "check that it does not point at a custom API",
        )
    ]
