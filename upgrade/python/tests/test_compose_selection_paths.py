# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose selections retain loader provenance across names and paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_explicit_dotenv_preflight import CLIENT

from daytona_upgrade.cli import main


@pytest.fixture(autouse=True)
def checkout_environment_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model a checkout boundary without invoking Git or relying on host paths.
    monkeypatch.setattr(
        "daytona_upgrade.project_discovery.environment_base", lambda base: base
    )
    monkeypatch.setattr("daytona_upgrade.project.environment_base", lambda base: base)


@pytest.mark.parametrize(
    "manifest",
    [
        "x: &key env_file\nservices: {app: {*key: runtime.conf}}\n",
        "x: &key env_file\nservices:\n  app:\n    *key: runtime.conf\n",
    ],
)
def test_reviews_when_a_compose_mapping_key_is_aliased(
    tmp_path: Path, manifest: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an aliased mapping key whose loader meaning cannot be certified.
    (tmp_path / "compose.yml").write_text(manifest)
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict preflight encounters unsupported YAML indirection.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the existing environment review remains visible.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize("filename", ["Dockerfile.conf", "Dockerfile", "runtime.conf"])
@pytest.mark.parametrize("outside", [False, True])
def test_refuses_when_selected_compose_has_a_custom_inline_url(
    tmp_path: Path, filename: str, outside: bool
) -> None:
    # Given an explicit manifest, including an absolute path outside a checkout.
    root = tmp_path / "project"
    root.mkdir()
    manifest = (tmp_path if outside else root) / filename
    files = {
        root / "app.py": CLIENT,
        root / "requirements.txt": "daytona==0.224.0\n",
        root / ".env": f"COMPOSE_FILE={manifest}\n",
        manifest: "services: {app: {environment: {DAYTONA_API_URL: https://self.example/api}}}\n",
    }
    for path, source in files.items():
        path.write_text(source)
    # When Compose provenance, rather than the basename, selects the parser.
    status = main([str(root), "--force", "--yes"])
    # Then the custom endpoint refuses atomically without a relative-path crash.
    assert status == 2
    assert {path: path.read_text() for path in files} == files


def test_reviews_when_selected_external_compose_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a missing absolute Compose input outside the checkout.
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text(f"COMPOSE_FILE={tmp_path / 'missing.conf'}\n")
    (root / "app.py").write_text(CLIENT)
    (root / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict preflight cannot read what Compose loads.
    status = main([str(root), "--force", "--yes", "--strict", "--json"])
    # Then uncertainty is review rather than a crash or silent success.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize("separator", [";", "|", ":"])
@pytest.mark.parametrize("quoted", [False, True])
def test_refuses_when_compose_uses_a_literal_path_separator(
    tmp_path: Path, separator: str, quoted: bool
) -> None:
    # Given the separator after COMPOSE_FILE in the same dotenv source.
    value = f"'{separator}'" if quoted else separator
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        ".env": f"COMPOSE_FILE=clean.conf{separator}custom.conf\nCOMPOSE_PATH_SEPARATOR={value}\n",
        "clean.conf": "services: {app: {image: app}}\n",
        "custom.conf": "services: {app: {env_file: runtime.conf}}\n",
        "runtime.conf": "DAYTONA_API_URL=https://self.example/api\n",
    }
    for name, source in files.items():
        (tmp_path / name).write_text(source)
    # When Compose selection honors the literal separator independently of order.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then no selected endpoint escapes refusal.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize("separator", ["${SEP}", '"${SEP}"', "$(separator)", ""])
def test_reviews_when_compose_path_separator_is_unverifiable(
    tmp_path: Path, separator: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an explicitly selected manifest list with an unknown separator.
    (tmp_path / ".env").write_text(
        f"COMPOSE_FILE=clean.conf;custom.conf\nCOMPOSE_PATH_SEPARATOR={separator}\n"
    )
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When preflight cannot prove which manifests Compose selects.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then it emits the existing environment review item.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize(
    "manifest",
    [
        "env_file: runtime.conf\nservices: {app: {image: app}}\n",
        "{env_file: runtime.conf, services: {app: {image: app}}}\n",
        "services: {app: {environment: {env_file: runtime.conf}}}\n",
        "services:\n  app:\n    environment:\n      env_file: runtime.conf\n",
    ],
)
def test_passes_strict_when_env_file_is_not_a_service_loader(
    tmp_path: Path, manifest: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a custom endpoint referenced only by an inert env_file key.
    (tmp_path / "compose.yml").write_text(manifest)
    (tmp_path / "runtime.conf").write_text("DAYTONA_API_URL=https://self.example/api\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration distinguishes service loader paths from other keys.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then unrelated data produces neither refusal nor a false review item.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
