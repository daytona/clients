# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Public CLI outcomes for proven and uncertain compound joins."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("uncertain", [False, True])
def test_cli_reports_join_evidence_when_upgrading_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], uncertain: bool
) -> None:
    # Given a real project whose only call follows either exhaustion or a break.
    source = (
        "from daytona import Daytona\n"
        "def launch(d: Daytona, values):\n    for item in values:\n"
        + (
            "        x = d\n        break\n"
            if uncertain
            else "        pass\n    else:\n        x = d\n"
        )
        + "    return x.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When running the upgrade through its public entry point in strict mode.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then uncertainty fails strict mode without changing executable code.
    report = json.loads(capsys.readouterr().out)
    assert status == int(uncertain)
    assert [item["kind"] for item in report["items"]] == (
        ["fixme"] if uncertain else []
    )
    expected = source if uncertain else source.replace("x.create()", "x.v0.create()")
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(expected))
