# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Large-project progress is visible without corrupting the JSON report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("count", [200, 201])
def test_progress_uses_stderr_when_discovery_exceeds_threshold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], count: int
) -> None:
    # Given exactly count discovered files, one of which imports the SDK.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nd = Daytona()\nd.create()\n"
    )
    for number in range(count - 1):
        (tmp_path / f"plain_{number:03}.py").write_text("value = 1\n")
    # When the real CLI emits a read-only JSON report.
    code = main([str(tmp_path), "--dry", "--json"])
    output = capsys.readouterr()
    # Then progress has its own stream and the report remains machine-readable.
    assert code == 0
    assert json.loads(output.out)["changedFiles"] == ["app.py"]
    assert bool(output.err) == (count > 200)
    if count > 200:
        assert str(count) in output.err
        assert str(tmp_path) not in output.err
