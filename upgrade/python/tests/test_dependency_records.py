# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Integrity-locked dependencies remain manual without blocking source migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from report_schema import validate_report

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "record",
    [
        "daytona==0.224.0 --hash=sha256:abcdef\n",
        "daytona --hash=sha256:abcdef\n",
        "daytona==0.224.0 \\\n    --hash=sha256:abcdef\n",
        "  Daytona_SDK[otel]==0.224 \\\r\n    --hash sha256:abcdef \\\r\n    --hash=sha256:123456 # keep\r\n",
        "daytona==0.224 \\\n    ; python_version > '3.9'\n",
        "daytona==0.224 \\\n",
    ],
)
@pytest.mark.parametrize("with_code", [False, True])
def test_preserves_record_when_manual_dependency_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], record: str, with_code: bool
) -> None:
    # Given
    manifest = tmp_path / "requirements.txt"
    source = "# header\r\n" + record + "requests==2.32\n"
    manifest.write_bytes(source.encode())
    if with_code:
        (tmp_path / "app.py").write_text(
            "from daytona import Daytona\nd = Daytona()\nd.create()\n"
        )
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert manifest.read_bytes() == source.encode()
    assert report["dependencies"] == []
    assert [(item["kind"], item["file"], item["line"]) for item in report["items"]] == [
        ("manual-dependency", "requirements.txt", 2)
    ]
    # The record's own extras are kept in the named requirement.
    expected = (
        "daytona[otel,v0-sdk]>=2,<3" if "[otel]" in record else "daytona[v0-sdk]>=2,<3"
    )
    assert expected in report["items"][0]["message"]
    assert "hash" in report["items"][0]["message"]
    validate_report(report)
    if with_code:
        assert "d.v0.create()" in (tmp_path / "app.py").read_text()


def test_migrates_other_dependency_when_one_record_is_manual(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    manifest = tmp_path / "requirements.txt"
    locked = "daytona==0.224 --hash=sha256:abc\n"
    manifest.write_text(locked + "daytona-sdk==0.225\n")
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert manifest.read_text() == locked + "daytona[v0-sdk]>=2,<3\n"
    assert len(report["dependencies"]) == 1
    assert len(report["items"]) == 1


def test_returns_manual_exit_when_strict_hash_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    source = "daytona==0.224 --hash=sha256:abc\n"
    manifest = tmp_path / "requirements.txt"
    manifest.write_text(source)
    # When
    code = main([str(tmp_path), "--json", "--strict"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "refused" not in report
    assert report["items"][0]["kind"] == "manual-dependency"
    assert manifest.read_text() == source
