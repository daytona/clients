# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only planned edits require a lossless decode; all failures are collected."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "name,data",
    [
        (".env", b"DB_PASSWORD=p\xe4ss\n"),
        ("legacy.py", b"# caf\xe9\nvalue = 1\n"),
        ("requirements-other.txt", b"# caf\xe9\nrequests\n"),
        ("pyproject.toml", b"# caf\xe9\n[project]\nname = 'demo'\n"),
        ("legacy.ipynb", b'{"cells": [], "metadata": {"note": "caf\xe9"}}'),
    ],
)
def test_migrates_when_invalid_encoding_needs_no_edit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, data: bytes
) -> None:
    # Given an unrelated Latin-1 file alongside a migratable project.
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    path = tmp_path / name
    path.write_bytes(data)
    # When migrating through the real writing CLI.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then migration succeeds without touching the scan-only input.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert not any(item["kind"] == "unsupported-file" for item in report["items"])
    assert "Daytona().v0.create()" in app.read_text()
    assert path.read_bytes() == data


@pytest.mark.parametrize("suffix", [".py", ".pyi"])
def test_reports_encoding_remedy_when_replacement_source_cannot_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], suffix: str
) -> None:
    # Given an invalid UTF-8 identifier alongside a valid, migratable file.
    path = tmp_path / ("legacy" + suffix)
    data = b"caf\xe9 = 1\n"
    path.write_bytes(data)
    app = tmp_path / "app.py"
    source = "from daytona import Daytona\nDaytona().create()\n"
    app.write_text(source)
    # When replacement decoding also makes the source unparsable.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the encoding remedy, not a generic parse error, blocks every write.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    failures = [item for item in report["items"] if item["kind"] == "unsupported-file"]
    assert [(item["file"], item["message"]) for item in failures] == [
        (
            path.name,
            f"{path.name} is not valid UTF-8 and declares no source encoding; convert it to UTF-8 or add a coding declaration, then rerun.",
        )
    ]
    assert path.read_bytes() == data
    assert app.read_text() == source


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize(
    "name,data",
    [
        ("app.py", b"# caf\xe9\nfrom daytona import Daytona\nDaytona().create()\n"),
        ("requirements.txt", b"# caf\xe9\ndaytona==0.224.0\n"),
        (
            "demo.ipynb",
            b'{"cells": [{"cell_type": "code", "source": "from daytona import Daytona\\nDaytona().create()\\n"}], "metadata": {"note": "caf\xe9"}}',
        ),
    ],
)
def test_refuses_every_file_when_planned_edits_need_invalid_encoding(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    count: int,
    name: str,
    data: bytes,
) -> None:
    # Given one or two undecodable files whose contents need migration.
    paths = [tmp_path / f"project{index}" / name for index in range(count)]
    for path in paths:
        path.parent.mkdir()
        path.write_bytes(data)
    # When calculating all outputs before writing.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then each bad edit is diagnosed with a root-relative path and nothing is written.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    failures = [item for item in report["items"] if item["kind"] == "unsupported-file"]
    assert [item["file"] for item in failures] == [
        path.relative_to(tmp_path).as_posix() for path in paths
    ]
    for path, item in zip(paths, failures):
        relative = path.relative_to(tmp_path).as_posix()
        assert item["message"].startswith(relative + " is not valid UTF-8")
        assert path.read_bytes() == data


@pytest.mark.parametrize(
    "declaration,diagnostic",
    [
        (
            b"",
            "src/app.py is not valid UTF-8 and declares no source encoding; convert it to UTF-8 or add a coding declaration, then rerun.",
        ),
        (
            b"# coding: invalid-encoding\n",
            "src/app.py has an invalid source encoding declaration; correct it or convert the file to UTF-8, then rerun.",
        ),
        (
            b"# coding: utf-8\n",
            "src/app.py is not valid UTF-8; convert it to UTF-8, then rerun.",
        ),
        (
            b"# coding: ascii\n",
            "src/app.py is not valid ascii; convert it to UTF-8, then rerun.",
        ),
    ],
)
def test_reports_relative_diagnostic_when_edited_source_cannot_decode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    declaration: bytes,
    diagnostic: str,
) -> None:
    # Given a nested edited file with an absent, invalid or incorrect declaration.
    path = tmp_path / "src" / "app.py"
    path.parent.mkdir()
    data = declaration + b"# caf\xe9\nfrom daytona import Daytona\nDaytona().create()\n"
    path.write_bytes(data)
    # When migrating through the writing CLI.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the actionable diagnostic identifies the root-relative file exactly.
    report = json.loads(capsys.readouterr().out)
    failures = [item for item in report["items"] if item["kind"] == "unsupported-file"]
    assert status == 1
    assert [(item["file"], item["message"]) for item in failures] == [
        ("src/app.py", diagnostic)
    ]
    assert path.read_bytes() == data
