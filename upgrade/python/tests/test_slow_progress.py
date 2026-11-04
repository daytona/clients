# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Slow small projects show progress without changing the CLI's report channel."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from threading import Timer

import pytest

from daytona_upgrade import project_progress
from daytona_upgrade.cli import main
from daytona_upgrade.flow import Flow


def test_progress_uses_stderr_when_a_small_project_outlasts_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given one SDK file and a deadline fired deterministically during analysis.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    timers: list[Timer] = []

    def schedule(self: Timer) -> None:
        timers.append(self)

    monkeypatch.setattr(Timer, "start", schedule)
    analyze = Flow.analyze

    def expire(self: Flow, source: str, module: ast.Module | None = None) -> None:
        if timers:
            timer = timers.pop()
            timer.function(*timer.args, **timer.kwargs)
        analyze(self, source, module)

    monkeypatch.setattr(Flow, "analyze", expire)
    monkeypatch.setattr(Timer, "join", lambda self: None)
    # When the CLI completes its read-only JSON run.
    code = main([str(tmp_path), "--dry", "--json"])
    output = capsys.readouterr()
    # Then the slow-analysis status is separate from a valid machine report.
    assert code == 0
    assert json.loads(output.out)["changedFiles"] == ["app.py"]
    assert output.err


def test_cancels_progress_when_scope_exits_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the real timer whose thread must not outlive a preflight.
    timers: list[Timer] = []
    start = Timer.start

    def track(self: Timer) -> None:
        timers.append(self)
        start(self)

    monkeypatch.setattr(Timer, "start", track)
    # When analysis finishes without reaching the deadline.
    with project_progress.slow_progress(lambda message: pytest.fail(message)):
        pass
    # Then its background timer has been cancelled and joined.
    assert len(timers) == 1
    assert not timers[0].is_alive()
