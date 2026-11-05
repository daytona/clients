# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Requirements include graphs are relative, deterministic and root contained."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "option",
    [
        "-r ",
        "-r",
        "--requirement ",
        "--requirement=",
        "-c ",
        "-c",
        "--constraint ",
        "--constraint=",
    ],
)
@pytest.mark.parametrize("with_code", [False, True])
def test_migrates_include_when_arbitrary_filename(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str, with_code: bool
) -> None:
    # Given
    (tmp_path / "deps").mkdir()
    (tmp_path / "requirements.txt").write_text(f"{option}deps/base.data # local\n")
    dependency = tmp_path / "deps/base.data"
    dependency.write_text("-r nested\ndaytona==0.224\n")
    nested = tmp_path / "deps/nested"
    nested.write_text("--requirement ../requirements.txt\ndaytona-sdk[otel]==0.223\n")
    if with_code:
        (tmp_path / "app.py").write_text(
            "from daytona import Daytona\nd = Daytona()\nd.create()\n"
        )
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    expected = (
        "daytona>=2,<3"
        if option.startswith(("-c", "--constraint"))
        else "daytona[v0-sdk]>=2,<3"
    )
    assert dependency.read_text() == f"-r nested\n{expected}\n"
    # An inherited constraint preserves existing extras but must not enable new ones.
    nested_extras = (
        "otel" if option.startswith(("-c", "--constraint")) else "otel,v0-sdk"
    )
    assert (
        nested.read_text()
        == f"--requirement ../requirements.txt\ndaytona[{nested_extras}]>=2,<3\n"
    )
    assert [entry["file"] for entry in report["dependencies"]] == [
        "deps/base.data",
        "deps/nested",
    ]
    if with_code:
        assert "d.v0.create()" in (tmp_path / "app.py").read_text()


@pytest.mark.parametrize(
    "target",
    [
        "../outside.txt",
        "/absolute",
        "link",
        "linked-dir/dep",
        "linked-dir/../hidden",
        ".venv/dep",
        "https://example.test/dep",
        "missing",
        '"unterminated',
    ],
)
def test_preserves_boundary_when_include_is_unsafe_or_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("daytona==0.224\n")
    (tmp_path / "dep").write_text("daytona==0.223\n")
    (root / "link").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    (root / ".venv").mkdir()
    generated = root / ".venv/dep"
    generated.write_text("daytona==0.223\n")
    (root / "hidden").write_text("daytona==0.222\n")
    (root / "requirements.txt").write_text(f"-r {target}\n")
    # When
    code = main([str(root), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["refused"]["reason"] == "no-daytona"
    assert outside.read_text() == "daytona==0.224\n"
    assert (tmp_path / "dep").read_text() == "daytona==0.223\n"
    assert generated.read_text() == "daytona==0.223\n"
    assert report["changedFiles"] == []


def test_migrates_include_when_quoted_name_and_continued_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "requirements.txt").write_text('-r \\\n "my deps.py"\n')
    dependency = tmp_path / "my deps.py"
    dependency.write_text("daytona==0.224\n")
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert dependency.read_text() == "daytona[v0-sdk]>=2,<3\n"
    assert report["changedFiles"] == ["my deps.py"]


def test_deduplicates_dependency_when_repeated_includes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "requirements.txt").write_text("-r deps\n-r ./deps\n-r deps\n")
    dependency = tmp_path / "deps"
    dependency.write_text("daytona==0.224\n")
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [entry["file"] for entry in report["dependencies"]] == ["deps"]
    assert dependency.read_text() == "daytona[v0-sdk]>=2,<3\n"
