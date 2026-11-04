# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Strict mode reports manual work even when an interactive write is declined."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from daytona_upgrade import cli


class TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(("strict", "expected_status"), [(True, 1), (False, 0)])
def test_declined_prompt_preserves_files_and_reports_manual_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict: bool,
    expected_status: int,
) -> None:
    # Given a project whose unproven receiver requires manual review.
    source = (
        "from daytona import Daytona\n"
        "class Custom(Daytona):\n"
        "    def run(self):\n"
        "        return super().create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    monkeypatch.setattr(sys, "stdin", TerminalInput("n\n"))
    arguments = [str(tmp_path), "--force"]
    if strict:
        arguments.append("--strict")

    # When the user declines the proposed source update.
    status = cli.main(arguments)

    # Then strict mode fails for manual work while neither mode writes files.
    assert status == expected_status
    assert app.read_text() == source
