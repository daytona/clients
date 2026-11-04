# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose scalar refusal fixtures mirrored by the TypeScript tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "environment",
    [
        'environment: {DAYTONA_API_URL: "https://self.hosted"}\n',
        'environment: {DAYTONA_API_URL: "https://self.hosted", OTHER: value}\n',
        'environment:\n  DAYTONA_API_URL: "https://self.hosted" # endpoint\n',
        "environment:\n  - DAYTONA_API_URL=https://self.hosted # endpoint\n",
    ],
)
def test_refuses_without_writing_when_compose_scalar_is_custom(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], environment: str
) -> None:
    # Given migratable source and dependency alongside the same Compose fixture as TS.
    originals = {
        "app.py": "from daytona import Daytona\nclient = Daytona()\nclient.create()\n",
        "requirements.txt": "daytona==0.224.0\n",
        "compose.yaml": environment,
    }
    for name, text in originals.items():
        (tmp_path / name).write_text(text)
    # When the real CLI is allowed to write.
    code = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then refusal precedes every write, including the dependency update.
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert [item["kind"] for item in report["items"]] == [
        "env-api-url",
        "custom-api-url",
    ]
    assert report["changedFiles"] == []
    assert {name: (tmp_path / name).read_text() for name in originals} == originals
