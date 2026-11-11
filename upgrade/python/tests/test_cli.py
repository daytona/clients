# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the real CLI against isolated project files and Git repositories."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from report_schema import validate_report

from daytona_upgrade.cli import main

SOURCE = "from daytona import Daytona\nd = Daytona()\nd.create()\n"


def test_upgrade_when_project_has_client(tmp_path: Path) -> None:
    # Given
    file = tmp_path / "app.py"
    file.write_text(SOURCE)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 0
    assert "d.v0.create()" in file.read_text()
    assert "daytona[v0-sdk]>=2,<3" in (tmp_path / "requirements.txt").read_text()


@pytest.mark.parametrize("flags", [["--dry"], ["--print"], ["--suggest-v2", "--json"]])
def test_readonly_when_preview_requested(
    tmp_path: Path, flags: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    file = tmp_path / "app.py"
    file.write_text(SOURCE)
    # When
    code = main([str(tmp_path), *flags])
    # Then
    assert code == 0
    assert file.read_text() == SOURCE
    assert capsys.readouterr().out


def test_schema_when_json_requested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "app.py").write_text(SOURCE)
    # When
    code = main([str(tmp_path), "--json", "--dry"])
    # Then
    report = json.loads(capsys.readouterr().out)
    validate_report(report)
    assert code == 0
    assert report["changedFiles"] == ["app.py"]
    assert report["dryRun"] is True


@pytest.mark.parametrize(
    "source", ["x = 1\n", "# daytona is mentioned but never imported\n"]
)
def test_refuses_when_no_daytona(
    tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "app.py").write_text(source)
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    assert code == 2
    assert json.loads(capsys.readouterr().out)["refused"]["reason"] == "no-daytona"


def test_transaction_refused_when_custom_url_in_later_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    first = tmp_path / "a.py"
    first.write_text(SOURCE)
    last = tmp_path / "z.py"
    custom = "from daytona import DaytonaConfig\nc = DaytonaConfig(api_url='https://private.example/api')\n"
    last.write_text(custom)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.1\n")
    # When
    code = main([str(tmp_path), "--json", "--drop-api-url"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert first.read_text() == SOURCE
    assert last.read_text() == custom
    assert requirements.read_text() == "daytona==0.1\n"


def test_preflight_when_later_python_invalid(tmp_path: Path) -> None:
    # Given
    first = tmp_path / "a.py"
    first.write_text(SOURCE)
    (tmp_path / "z.py").write_text("from daytona import Daytona\n(\n")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 1
    assert first.read_text() == SOURCE


def test_dirty_git_when_untracked_files_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
    )
    (tmp_path / "app.py").write_text(SOURCE)
    # When
    code = main([str(tmp_path), "--json"])
    # Then
    assert code == 2
    assert json.loads(capsys.readouterr().out)["refused"]["reason"] == "dirty-tree"


@pytest.mark.parametrize("flag", ["--dry", "--force", "--print"])
def test_git_safety_when_preview_or_override_requested(
    tmp_path: Path, flag: str
) -> None:
    # Given
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
    )
    (tmp_path / "app.py").write_text(SOURCE)
    # When
    code = main([str(tmp_path), flag, "--yes"])
    # Then
    assert code == 0
    if flag != "--force":
        assert (tmp_path / "app.py").read_text() == SOURCE


@pytest.mark.parametrize(
    "flags", [["--unknown"], ["--suggest-v2", "--remove-suggestions"]]
)
def test_refuses_when_arguments_invalid(
    flags: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    code = main([*flags, "--json"])
    # Then
    assert code == 2
    assert json.loads(capsys.readouterr().out)["refused"]["reason"] == "bad-arguments"


def test_strict_when_environment_manual_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "app.py").write_text(SOURCE)
    (tmp_path / ".env.local").write_text("DAYTONA_API_URL=https://app.daytona.io/api\n")
    # When
    code = main([str(tmp_path), "--dry", "--json", "--strict"])
    # Then
    assert code == 1
    assert json.loads(capsys.readouterr().out)["items"][0]["kind"] == "env-api-url"


def test_discovery_when_generated_and_linked_files_exist(tmp_path: Path) -> None:
    # Given
    (tmp_path / "app.py").write_text(SOURCE)
    ignored = tmp_path / ".venv"
    ignored.mkdir()
    (ignored / "bad.py").write_text("( invalid")
    (tmp_path / "linked.py").symlink_to(ignored / "bad.py")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 0
    assert (ignored / "bad.py").read_text() == "( invalid"


def test_module_entry_when_invoked_as_python_module(tmp_path: Path) -> None:
    # Given
    (tmp_path / "app.py").write_text(SOURCE)
    # When
    result = subprocess.run(
        [sys.executable, "-m", "daytona_upgrade", str(tmp_path), "--dry", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then
    assert result.returncode == 0
    assert json.loads(result.stdout)["tool"] == "daytona-upgrade"


def test_other_ecosystem_when_javascript_manifest_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "app.py").write_text(SOURCE)
    (tmp_path / "package.json").write_text('{"dependencies":{"@daytonaio/sdk":"0.1"}}')
    # When
    code = main([str(tmp_path), "--dry", "--json"])
    # Then
    assert code == 0
    assert any(
        item["kind"] == "other-ecosystem"
        for item in json.loads(capsys.readouterr().out)["items"]
    )


@pytest.mark.parametrize(
    "url,flags",
    [("'https://app.daytona.io/api'", []), ("cloud_url", ["--drop-api-url"])],
)
def test_drops_api_url_when_cloud_is_asserted(
    tmp_path: Path, url: str, flags: list[str]
) -> None:
    # Given
    file = tmp_path / "app.py"
    file.write_text(
        f"from daytona import DaytonaConfig\nc = DaytonaConfig(api_url={url})\n"
    )
    # When
    code = main([str(tmp_path), "--yes", *flags])
    # Then
    assert code == 0
    assert "api_url=" not in file.read_text()


def test_idempotent_when_dependency_already_upgraded(tmp_path: Path) -> None:
    # Given
    file = tmp_path / "requirements.txt"
    source = "daytona[v0-sdk]>=2,<3\n"
    file.write_text(source)
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 0
    assert file.read_text() == source


def test_no_dependency_when_daytona_only_in_manifest_comment(tmp_path: Path) -> None:
    # Given
    (tmp_path / "requirements.txt").write_text("# daytona will be added later\nnumpy\n")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 2


def test_no_partial_write_when_later_notebook_malformed(tmp_path: Path) -> None:
    # Given
    file = tmp_path / "app.py"
    file.write_text(SOURCE)
    (tmp_path / "z.ipynb").write_text('{"cells":[{"cell_type":"code","source":[42]}]}')
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 1
    assert file.read_text() == SOURCE


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        "compose.yml",
        "docker-compose.dev.yaml",
        "Dockerfile.dev",
        "k8s/service.yaml",
        "helm/values.yml",
        "charts/app/values.yaml",
        "deploy/config.yml",
        ".github/workflows/test.yaml",
    ],
)
def test_environment_scan_when_supported_deployment_file(
    tmp_path: Path, relative: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / "app.py").write_text(SOURCE)
    file = tmp_path / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    source = "DAYTONA_SERVER_URL=https://app.daytona.io/api\n"
    file.write_text(source)
    # When
    code = main([str(tmp_path), "--dry", "--json"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert any(
        item["kind"] == "env-api-url" and item["file"] == relative
        for item in report["items"]
    )
    assert file.read_text() == source


@pytest.mark.parametrize("flag", ["--help", "-h", "--version", "-v"])
def test_information_when_help_or_version_requested(flag: str) -> None:
    # Given / When
    code = main([flag])
    # Then
    assert code == 0


@pytest.mark.parametrize("extension", [".py", ".pyi", ".pyw"])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "latin-1"])
def test_preserves_source_bytes_when_crlf_and_encoding_declared(
    tmp_path: Path, extension: str, encoding: str
) -> None:
    # Given
    file = tmp_path / ("app" + extension)
    cookie = "utf-8" if encoding == "utf-8-sig" else encoding
    original = f"# coding: {cookie}\r\n# caf\u00e9\r\nfrom daytona import Daytona\r\nd = Daytona()\r\nd.create()\r\n"
    file.write_bytes(original.encode(encoding))
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 0
    assert file.read_bytes() == original.replace("d.create()", "d.v0.create()").encode(
        encoding
    )


def test_preserves_manifest_bytes_when_requirements_use_crlf(tmp_path: Path) -> None:
    # Given
    file = tmp_path / "requirements.txt"
    file.write_bytes(b"# untouched comment\r\ndaytona==0.224.0\r\nnumpy>=1\r\n")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then
    assert code == 0
    assert (
        file.read_bytes()
        == b"# untouched comment\r\ndaytona[v0-sdk]>=2,<3\r\nnumpy>=1\r\n"
    )


@pytest.mark.parametrize("initializer", ["__init__.py", "__init__.pyw"])
def test_migrates_relative_imports_from_a_package_initializer(
    tmp_path: Path, initializer: str
) -> None:
    # Given a package whose initializer re-exports the client class relatively.
    package = tmp_path / "pkg"
    package.mkdir()
    (package / initializer).write_text("from .clients import Client\n")
    (package / "clients.py").write_text("from daytona import Daytona as Client\n")
    app = tmp_path / "app.py"
    app.write_text("from pkg import Client\nClient().create()\n")
    # When
    code = main([str(tmp_path), "--yes"])
    # Then the call through the initializer's relative import is migrated.
    assert code == 0
    assert app.read_text() == "from pkg import Client\nClient().v0.create()\n"
