# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose loader boundaries must neither hide endpoints nor invent review."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_explicit_dotenv_preflight import CLIENT

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "scalar",
    ["app}", "app]", "app{", "app[", "app {text", "app [text", "'app{}[]'"],
)
def test_refuses_when_plain_metadata_precedes_services(
    tmp_path: Path, scalar: str
) -> None:
    # Given delimiters that are scalar content, not flow collection indicators.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        "staging.yml": f"name: {scalar}\nservices:\n  app:\n    env_file: runtime.conf\n",
        "runtime.conf": "DAYTONA_API_URL=https://self.example/api\n",
    }
    for name, source in files.items():
        (tmp_path / name).write_text(source)

    # When migration inspects a custom-named Compose mapping.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then the custom endpoint refuses before any file is written.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "manifest",
    [
        "x-base: &base {image: app}\nservices: {app: {<<: *base, command: env_file}}\n",
        'x-base: &base app\nservices: {app: {command: "env_file: missing.conf"}}\n',
        "x-base: &base app\nservices:\n  app:\n    command: echo {env_file: missing.conf}\n",
        "x-base: &base app\nservices: {app: {command: [env_file, other]}}\n",
        "x-base: &base app\nservices:\n  app:\n    command: |\n      env_file: missing.conf\n",
        "x-base: &base app\nservices: {app: {image: app}} # env_file: *base\n",
        "x-base: &base app\nservices:\n  app:\n    env_file:missing.conf\n",
        "x-base: &base app\nx-env:\n  env_file:\nservices: {app: {image: app}}\n",
    ],
)
def test_passes_strict_when_anchored_yaml_has_no_env_file_key(
    tmp_path: Path, manifest: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given anchors alongside env_file text that is not a mapping key.
    (tmp_path / "staging.yml").write_text(manifest)
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When strict migration scans the deployment.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then inert text does not create manual API-URL review.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []


@pytest.mark.parametrize(
    "entry",
    [
        "services:\n  app:\n    env_file: clean.conf\n",
        'services: {app: {"env_file": clean.conf}}\n',
        'services: {app: {"env\\u005ffile": clean.conf}}\n',
    ],
)
def test_reviews_when_anchored_yaml_has_an_env_file_key(
    tmp_path: Path, entry: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an actual block or flow key and YAML loader indirection.
    (tmp_path / "staging.yml").write_text("x-base: &base app\n" + entry)
    (tmp_path / "clean.conf").write_text("OTHER=value\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When strict migration cannot verify advanced YAML semantics.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then even a clean literal candidate retains the existing review item.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize("custom_base", [".", "first", "second"])
def test_refuses_when_any_compose_base_has_a_custom_endpoint(
    tmp_path: Path, custom_base: str
) -> None:
    # Given clean alternatives and a custom endpoint at one plausible base.
    files = {"app.py": CLIENT, "requirements.txt": "daytona==0.224.0\n"}
    for directory in [".", "first", "second"]:
        (tmp_path / directory).mkdir(exist_ok=True)
        files[f"{directory}/runtime.conf"] = (
            "DAYTONA_API_URL=https://self.example/api\n"
            if directory == custom_base
            else "OTHER=value\n"
        )
    files["first/override.yml"] = "services: {app: {env_file: runtime.conf}}\n"
    files["second/base.yml"] = "services: {app: {image: app}}\n"
    for name, source in files.items():
        (tmp_path / name).write_text(source)

    # When the CLI considers every manifest directory and the project root.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then no earlier clean candidate masks the custom endpoint.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize("manifest", ["compose.yml", "infra/override.yml"])
def test_reviews_when_no_env_file_candidate_exists(
    tmp_path: Path, manifest: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a literal reference whose candidates are all missing.
    path = tmp_path / manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("services: {app: {env_file: missing.conf}}\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When strict migration attempts to verify the environment.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then absence is unresolved review, never a silent pass.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize(
    "setting",
    [
        f"COMPOSE_FILE=infra/base.conf{os.pathsep}overrides/runtime\n",
        f'export COMPOSE_FILE="infra/base.conf{os.pathsep}overrides/runtime" # deployment\n',
        f"COMPOSE_FILE='infra/base.conf{os.pathsep}overrides/runtime'\n",
    ],
)
@pytest.mark.parametrize(
    "loader",
    [
        "services: {app: {env_file: runtime.conf}}",
        "services:\n  app:\n    env_file: runtime.conf",
    ],
)
def test_refuses_when_dotenv_names_manifests_without_yaml_extensions(
    tmp_path: Path, setting: str, loader: str
) -> None:
    # Given explicit Compose paths with flow or block service declarations.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        ".env": setting,
        "infra/base.conf": "services: {app: {image: app}}\n",
        "infra/runtime.conf": "DAYTONA_API_URL=https://self.example/api\n",
        "overrides/runtime": loader + "\n",
        "overrides/runtime.conf": "OTHER=value\n",
    }
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)

    # When discovery honors COMPOSE_FILE before classifying file extensions.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then the first manifest's directory is also a candidate for the override.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files
