# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unreadable encodings become structured input failures without partial writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "name,content",
    [
        ("broken.py", b"from daytona import Daytona\nDaytona().create()\n"),
        ("broken.pyi", b"from daytona import Sandbox\n"),
        (
            "setup.py",
            b"from setuptools import setup\nsetup(install_requires=['daytona'])\n",
        ),
        ("requirements.txt", b"daytona==0.224.0\n"),
        ("pyproject.toml", b"[project]\ndependencies = ['daytona']\n"),
        ("setup.cfg", b"[options]\ninstall_requires = daytona\n"),
        ("Pipfile", b"[packages]\ndaytona = '*'\n"),
    ],
)
@pytest.mark.parametrize("prefix", [b"", b"# valid first line\n# valid second line\n"])
def test_unsupported_file_when_input_has_invalid_utf8(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    content: bytes,
    prefix: bytes,
) -> None:
    # Given migratable code and one edited non-UTF-8 input with no declaration.
    originals = {
        "app.py": b"from daytona import Daytona\nDaytona().list()\n",
        "requirements.txt": b"daytona==0.224.0\n",
        name: prefix + b"# caf\xe9\n" + content,
    }
    for relative, data in originals.items():
        (tmp_path / relative).write_bytes(data)
    # When the CLI is allowed to write.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then an unsupported-file item blocks every write and gives actionable guidance.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "refused" not in report
    assert report["changedFiles"] == []
    failures = [item for item in report["items"] if item["kind"] == "unsupported-file"]
    assert [(item["file"], item["kind"]) for item in failures] == [
        (name, "unsupported-file")
    ]
    assert "convert it to UTF-8" in failures[0]["message"]
    assert "codec can't decode" not in json.dumps(report)
    assert {
        relative: (tmp_path / relative).read_bytes() for relative in originals
    } == originals


def test_unsupported_file_when_included_requirement_has_invalid_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an extensionless requirements include.
    (tmp_path / "requirements.txt").write_text("-r deps\ndaytona==0.224.0\n")
    (tmp_path / "deps").write_bytes(b"# caf\xe9\ndaytona==0.224.0\n")
    # When discovering includes through the real CLI.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the included file, not a raw decoder exception, is reported.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["file"], item["kind"]) for item in report["items"]] == [
        ("deps", "unsupported-file")
    ]
    assert (tmp_path / "requirements.txt").read_text() == "-r deps\ndaytona==0.224.0\n"


@pytest.mark.parametrize("name", ["requirements.txt", "setup.py", "pyproject.toml"])
def test_manual_dependency_when_ancestor_manifest_has_invalid_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given a partial-root scan with an unreadable read-only ancestor manifest.
    (tmp_path / ".git").mkdir()
    (tmp_path / name).write_bytes(b"# caf\xe9\n")
    root = tmp_path / "src"
    root.mkdir()
    app = root / "app.py"
    source = "from daytona import Daytona\nDaytona().list()\n"
    app.write_text(source)
    # When checking the consuming dependency before writing.
    status = main([str(root), "--json", "--force", "--yes"])
    # Then existing read-only ancestor handling retains its manual fallback.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [item["kind"] for item in report["items"]] == [
        "manual-dependency",
        "manual-dependency",
    ]
    assert any(item["file"] == "../" + name for item in report["items"])
    assert "Daytona().v0.list()" in app.read_text()
