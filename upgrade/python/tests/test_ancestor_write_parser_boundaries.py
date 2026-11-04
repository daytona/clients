# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regressions for ancestor bounds, write errors, and parser fallbacks."""

from __future__ import annotations

import ast
import errno
from pathlib import Path

import pytest

from daytona_upgrade.partial_analysis import partial_analysis
from daytona_upgrade.writes import target_error


@pytest.mark.parametrize("distance", [0, 5, 6])
def test_checks_five_parents_when_selected_root_is_file(
    tmp_path: Path, distance: int
) -> None:
    # Given a non-Git selection with a manifest at the containing or ancestor root.
    (tmp_path / "requirements.txt").write_text("daytona<1\n")
    directory = tmp_path.joinpath(*[f"level{index}" for index in range(distance)])
    directory.mkdir(parents=True, exist_ok=True)
    selected = directory / "app.py"
    selected.write_text("pass\n")
    # When inspecting omitted dependency context.
    items = partial_analysis(selected)
    # Then the containing directory does not consume one of five parent levels.
    assert bool(items) is (distance <= 5)


@pytest.mark.parametrize("number", [errno.EACCES, errno.ENOSPC, errno.ENOENT])
def test_preserves_os_fields_when_write_error_is_wrapped(
    tmp_path: Path, number: int
) -> None:
    # Given a low-level failure naming an internal staging file.
    error = OSError(number, "write failed", str(tmp_path / ".staged.tmp"))
    target = tmp_path / "app.py"
    # When adding logical target context.
    wrapped = target_error(target, error, tmp_path)
    # Then OS classification and diagnostic fields survive without staging leakage.
    assert isinstance(wrapped, type(error))
    assert wrapped.errno == number
    assert wrapped.strerror == error.strerror
    assert wrapped.filename == "app.py"
    assert (
        str(wrapped)
        == "app.py could not be written: write failed; fix the problem, then rerun."
    )


@pytest.mark.parametrize(
    "case",
    [
        ("import daytona", True),
        ("import daytona_sdk", True),
        ("from daytona.sandbox import Sandbox", True),
        ("from daytona_sdk import Daytona", True),
        ("import requests", False),
        ("import daytona_helper", False),
    ],
)
def test_detects_import_when_sibling_uses_newer_python_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[str, bool]
) -> None:
    # Given a checkout sibling that Python 3.9 cannot parse.
    statement, expected = case
    (tmp_path / ".git").mkdir()
    selected = tmp_path / "service"
    selected.mkdir()
    (tmp_path / "shared.py").write_text(
        f"{statement}\nmatch value:\n    case _: pass\n"
    )
    parse = ast.parse

    def parse_python39(source: str) -> ast.Module:
        return parse(source, feature_version=(3, 9))

    # When inspecting omitted context under the Python 3.9 grammar.
    with monkeypatch.context() as patch:
        patch.setattr(ast, "parse", parse_python39)
        items = partial_analysis(selected)
    # Then unsupported syntax cannot hide textual Daytona import evidence.
    assert bool(items) is expected
