# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Scan-root coordinates and single-file checkout context regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.partial_analysis import partial_analysis
from daytona_upgrade.project import preflight


@pytest.mark.parametrize("nested", [False, True])
def test_reports_partial_when_single_file_has_checkout_sibling(
    tmp_path: Path, nested: bool
) -> None:
    # Given a checkout without manifests and two sources in the same directory.
    (tmp_path / ".git").mkdir()
    directory = tmp_path / "src" if nested else tmp_path
    directory.mkdir(exist_ok=True)
    selected = directory / "app.py"
    selected.write_text("from daytona import Daytona\nDaytona().create()\n")
    (directory / "shared.py").write_text(
        "from daytona import Daytona\napi = Daytona()\n"
    )
    # When targeting only one source.
    items = partial_analysis(selected)
    # Then its containing directory is not excluded from read-only context discovery.
    assert [item["kind"] for item in items] == ["types-unresolved"]


def test_avoids_partial_when_only_selected_file_uses_sdk(tmp_path: Path) -> None:
    # Given a checkout whose only SDK source is the selected file.
    (tmp_path / ".git").mkdir()
    selected = tmp_path / "app.py"
    selected.write_text("from daytona import Daytona\n")
    # When inspecting context outside that exact file.
    items = partial_analysis(selected)
    # Then the file cannot count as its own omitted context.
    assert items == ()


@pytest.mark.parametrize("single_file", [False, True])
@pytest.mark.parametrize("manifest", ["malformed", "override"])
def test_uses_scan_root_when_ancestor_diagnostic_has_nested_owner(
    tmp_path: Path, single_file: bool, manifest: str
) -> None:
    # Given an ancestor issue reported while migrating a nested source owner.
    (tmp_path / ".git").mkdir()
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[project\n"
        if manifest == "malformed"
        else '[project]\ndependencies=["daytona[v0-sdk]>=2"]\n[tool.uv.sources]\ndaytona={path="../sdk"}\n'
    )
    root = tmp_path / "app"
    owner = root / "nested"
    owner.mkdir(parents=True)
    (owner / "requirements.txt").write_text("requests>=2\n")
    source = owner / "main.py"
    source.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When preflighting a directory or a single file with read-only ancestor context.
    result = preflight(
        source if single_file else root, TransformOptions(load_mapping())
    )
    # Then all paths use the selected scan's coordinate system, not the owner's.
    expected = "../../pyproject.toml" if single_file else "../pyproject.toml"
    diagnostics = [
        item
        for item in result.items
        if item["kind"] == "manual-dependency"
        and item["file"].endswith("pyproject.toml")
    ]
    assert [item["file"] for item in diagnostics] == [expected]
    if manifest == "malformed":
        assert expected in diagnostics[0]["message"]


def test_strict_succeeds_when_quoted_literal_is_only_annotation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a complete dependency plus metadata that resembles an SDK type.
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "app.py").write_text(
        "import daytona\nfrom typing import Literal\ndef f(x: \"Literal['Sandbox']\"): pass\n"
    )
    # When driving the strict CLI, not just its transform helper.
    status = main([str(tmp_path), "--force", "--strict", "--json"])
    # Then false annotation diagnostics cannot make strict migration fail.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
