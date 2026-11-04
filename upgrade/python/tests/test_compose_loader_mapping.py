# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose declarations retain endpoint refusal through bounded YAML parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_explicit_dotenv_preflight import CLIENT, LOADERS

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "manifest",
    [
        "services:\n  app: {image: app, env_file: runtime.conf}\n",
        "services:\n  app: {env_file: [clean.conf, runtime.conf], image: app}\n",
        "services: {app: {env_file: runtime.conf}}\n",
        "{services: {app: {env_file: runtime.conf}}}\n",
        'services: {app: {"env_file": [{path: "runtime.conf", required: false}]}}\n',
        "services: {app: {env_file: [\n clean.conf,\n runtime.conf\n]}}\n",
        "services:\n  app:\n    env_file: runtime.conf\n",
        'services: {app: {env_file: "runtime,{}.conf"}}\n',
        'services:\n  app:\n    "env\\u005ffile": runtime.conf\n',
        "services: {app: {environment: {DAYTONA_API_URL: 'https://self.example/api'}}}\n",
    ],
)
@pytest.mark.parametrize("filename", ["compose.yml", "infra/staging.yml"])
def test_refuses_without_writes_when_compose_mapping_loads_custom_endpoint(
    tmp_path: Path, manifest: str, filename: str
) -> None:
    # Given real deployment inputs, including a custom-named Compose manifest.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        filename: manifest,
    }
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    directory = (tmp_path / filename).parent
    for name in ["runtime.conf", "runtime,{}.conf"]:
        (directory / name).write_text("DAYTONA_API_URL=https://self.example/api\n")
    (directory / "clean.conf").write_text("OTHER=value\n")

    # When the public CLI follows literal loader declarations.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then endpoint refusal precedes every planned write.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "manifest",
    [
        "services: {app: {env_file: *runtime}}\n",
        "x-env: &runtime clean.conf\nservices: {app: {env_file: *runtime}}\n",
        "x: &base {env_file: clean.conf}\nservices: {app: {<<: *base}}\n",
        "---\nservices: {app: {env_file: clean.conf}}\n---\nservices: {}\n",
        "services: {app: {env_file: !!str clean.conf}}\n",
        "services: {app: {env_file: ${FILE}}}\n",
        "services: {app: {env_file: [clean.conf, *other]}}\n",
        'services: {app: {env_file: "clean.conf" trailing}}\n',
        "services: {app: {env_file: [clean.conf] trailing}}\n",
        "services: {app: {env_file: {unexpected: clean.conf}}}\n",
        "services: {app: {env_file: [clean.conf}\n",
    ],
)
def test_reviews_when_compose_loader_cannot_be_proven(
    tmp_path: Path, manifest: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a clean literal candidate but YAML outside the supported subset.
    (tmp_path / "compose.yml").write_text(manifest)
    (tmp_path / "clean.conf").write_text("OTHER=value\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When strict migration cannot prove the loaded environment.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then existing clean candidates never erase unresolved loader review.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize("filename", [".env.example", ".env.sample", ".env.template"])
@pytest.mark.parametrize("loader", [*LOADERS, "compose"])
def test_refuses_when_sample_file_is_explicitly_loaded(
    tmp_path: Path, filename: str, loader: str
) -> None:
    # Given sample-named data that the application explicitly loads.
    source = (
        "" if loader == "compose" else loader.replace("deployment.conf", filename)
    ) + CLIENT
    files = {"app.py": source, "requirements.txt": "daytona==0.224.0\n"}
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    (tmp_path / filename).write_text("DAYTONA_API_URL=https://self.example/api\n")
    if loader == "compose":
        (tmp_path / "compose.yml").write_text(
            f"services: {{app: {{env_file: {filename}}}}}\n"
        )

    # When loader provenance takes precedence over basename discovery.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then explicit samples refuse before writes, just like active dotenv files.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "manifest",
    [
        'services: {app: {command: "env_file: missing.conf"}}\n',
        "services:\n  app:\n    command: |\n      {env_file: missing.conf}\n",
        "services: {app: {env_file: [clean.conf]}} # env_file: *unknown\n",
        "apiVersion: v1\nkind: ConfigMap\ndata:\n  services: {env_file: missing.conf}\n",
    ],
)
def test_passes_when_custom_yaml_contains_no_active_compose_loader(
    tmp_path: Path, manifest: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given inert text or a non-Compose manifest outside deployment discovery.
    (tmp_path / "staging.yml").write_text(manifest)
    (tmp_path / "clean.conf").write_text("OTHER=value\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When strict preflight scans actual Compose declarations only.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then unrelated YAML and inert strings do not invent environment review.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
