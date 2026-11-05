# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Factory convergence revisits dependencies rather than the whole project."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from time import perf_counter

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import Flow


@pytest.mark.wall_clock
def test_requires_review_quickly_when_factory_chain_opposes_module_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given 200 forward factory links in 50 consumer-before-provider modules.
    for module in range(50):
        start, end = module * 4, (module + 1) * 4
        source = (
            f"from provider{module + 1:02} import f{end}\n"
            if module < 49
            else "from daytona import Daytona\n"
        )
        source += "".join(
            f"def f{number}():\n    return "
            + (f"f{number + 1}()" if number < 199 else "Daytona()")
            + "\n"
            for number in range(start, end)
        )
        (tmp_path / f"provider{module:02}.py").write_text(source)
    (tmp_path / "app.py").write_text(
        "from provider00 import f0\nclient = f0()\nclient.create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    analyses = 0
    analyze = Flow.analyze

    def count_analysis(
        self: Flow, source: str, module: ast.Module | None = None
    ) -> None:
        nonlocal analyses
        analyses += 1
        analyze(self, source, module)

    monkeypatch.setattr(Flow, "analyze", count_analysis)

    # When the real CLI computes summaries and migrates the linked call.
    started = perf_counter()
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    elapsed = perf_counter() - started

    # Then review survives without a project-wide scan for each new summary.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["kind"], item["file"]) for item in report["items"]] == [
        ("fixme", "app.py")
    ]
    assert "# FIXME(daytona-upgrade):" in (tmp_path / "app.py").read_text()
    assert analyses < 1000, f"factory chain required {analyses} module analyses"
    assert elapsed < 20, f"factory chain took {elapsed:.2f}s"
