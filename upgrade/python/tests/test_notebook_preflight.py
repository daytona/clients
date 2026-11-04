# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook failures retain all preflight diagnostics and block every write."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.notebook_json import SourceValueSpan
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "source",
    [
        "{",
        "{}",
        "[]",
        '{"cells":42}',
        '{"cells":[42]}',
        '{"cells":[{"cell_type":"code","source":42}]}',
        '{"cells":[{"cell_type":"code","source":[42]}]}',
    ],
)
def test_blocks_writes_when_notebook_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    # Given a malformed notebook alongside migratable code.
    notebook = tmp_path / "broken.ipynb"
    notebook.write_text(source)
    app = tmp_path / "app.py"
    original = "from daytona import Daytona\nDaytona().create()\n"
    app.write_text(original)
    # When the writing CLI scans the project.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the notebook is reported and no file is written.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert len(report["items"]) == 1
    assert report["items"][0]["file"] == "broken.ipynb"
    assert report["items"][0]["kind"] == "unsupported-file"
    assert report["items"][0]["message"].startswith(
        "broken.ipynb is not a valid notebook: "
    )
    assert notebook.read_text() == source
    assert app.read_text() == original


def test_custom_url_refuses_when_notebook_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an invalid notebook and a readable environment with a custom URL.
    (tmp_path / "broken.ipynb").write_text("{")
    environment = tmp_path / ".env"
    original = "DAYTONA_API_URL=https://self.hosted/api\n"
    environment.write_text(original)
    # When
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the custom URL wins without hiding the notebook diagnostic.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert ("broken.ipynb", "unsupported-file") in [
        (item["file"], item["kind"]) for item in report["items"]
    ]
    assert environment.read_text() == original


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user (root can read chmod 000 paths)",
)
def test_reports_both_items_when_notebook_is_invalid_and_directory_is_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an invalid notebook and a real unreadable directory.
    (tmp_path / "broken.ipynb").write_text("{")
    locked = tmp_path / "secret"
    locked.mkdir()
    locked.chmod(0)
    try:
        # When
        status = main([str(tmp_path), "--json", "--force", "--yes"])
    finally:
        locked.chmod(0o700)
    # Then neither diagnostic is lost to the other failure.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["changedFiles"] == []
    assert {(item["file"], item["kind"]) for item in report["items"]} == {
        ("broken.ipynb", "unsupported-file"),
        ("secret", "unsupported-file"),
    }


def test_continues_scan_when_notebook_transform_cannot_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a notebook accepted by the index reader but rejected by the source parser.
    (tmp_path / "broken.ipynb").write_text('{"cells":[]}')
    # And a migratable source and manifest whose edits would otherwise be planned.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nDaytona().create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    environment = tmp_path / "later" / ".env"
    environment.parent.mkdir()
    environment.write_text("DAYTONA_API_URL=https://self.hosted/api\n")

    def fail(source: str) -> tuple[SourceValueSpan | None, ...]:
        raise json.JSONDecodeError("source parser failed", source, 0)

    monkeypatch.setattr("daytona_upgrade.notebooks.cell_source_spans", fail)
    # When the transform pass encounters a notebook parse failure.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the scan still discovers the URL and discards every planned write.
    assert result.changes == ()
    assert result.dependencies == ()
    assert {(item["file"], item["kind"]) for item in result.items} == {
        ("broken.ipynb", "unsupported-file"),
        ("later/.env", "custom-api-url"),
        ("later/.env", "env-api-url"),
    }
