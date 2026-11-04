# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Deployment-file provenance survives decorators and Compose indirection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_dotenv_search_and_settings_overrides import inspect_loader
from test_explicit_dotenv_preflight import CLIENT

from daytona_upgrade.cli import main


@pytest.mark.parametrize("module", ["pydantic", "pydantic_settings"])
@pytest.mark.parametrize(
    "settings",
    [
        'settings = Settings(_env_file="config/deployment.conf")\n',
        'Alias = Settings\nsettings = Alias(_env_file="config/deployment.conf")\n',
        'class Child(Settings): pass\nsettings = Child(_env_file="config/deployment.conf")\n',
        'class Child(Settings):\n    class Config:\n        env_file = "config/deployment.conf"\n',
    ],
)
def test_refuses_without_writes_when_decorated_settings_load_custom_url(
    tmp_path: Path, module: str, settings: str
) -> None:
    # Given a decorator that may preserve or replace a settings class.
    source = (
        f"from {module} import BaseSettings\n"
        "@registered\nclass Settings(BaseSettings): pass\n" + settings + CLIENT
    )
    files = {"app.py": source, "requirements.txt": "daytona==0.224.0\n"}
    (tmp_path / "config").mkdir()
    (tmp_path / "config/deployment.conf").write_text(
        "DAYTONA_API_URL=https://self.example/api\n"
    )
    for name, text in files.items():
        (tmp_path / name).write_text(text)

    # When the public CLI scans the literal deployment input.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then refusal precedes every source and dependency write.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


def test_reviews_when_decorated_settings_env_file_is_dynamic(tmp_path: Path) -> None:
    # Given a decorated settings constructor whose file is selected at runtime.
    loader = (
        "from pydantic_settings import BaseSettings\n"
        "@registered\nclass Settings(BaseSettings): pass\n"
        "settings = Settings(_env_file=selected)\n"
    )

    # When environment-only provenance follows the decorated binding.
    items = inspect_loader(tmp_path, loader)

    # Then unresolved file selection retains environment review.
    assert [item["kind"] for item in items] == ["env-api-url"]


@pytest.mark.parametrize(
    "entry",
    [
        "config/deployment.conf",
        "'config/deployment.conf' # deployment settings",
        "[clean.conf, config/deployment.conf]",
        "\n      - clean.conf\n      - config/deployment.conf",
        "\n      - path: config/deployment.conf\n        required: true",
        "\n      - path: config/deployment.conf\n        required: false",
        "\n      - required: true\n        path: config/deployment.conf",
        "[{path: config/deployment.conf, required: true}]",
        "[\n      clean.conf,\n      config/deployment.conf\n    ]",
        "\n    - config/deployment.conf",
        '"config/deployment.conf"',
    ],
)
def test_refuses_without_writes_when_compose_loads_custom_url(
    tmp_path: Path, entry: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an otherwise undiscovered file resolved from a nested Compose directory.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        "deploy/compose.yaml": f"services:\n  app:\n    env_file: {entry}\n",
        "deploy/clean.conf": "OTHER=value\n",
        "deploy/config/deployment.conf": "DAYTONA_API_URL=https://self.example/api\n",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    # When the CLI follows Compose's environment file declarations.
    status = main([str(tmp_path), "--force", "--yes", "--json"])

    # Then custom evidence points to the loaded file and the transaction is untouched.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert any(
        item["file"] == "deploy/config/deployment.conf"
        and item["kind"] == "custom-api-url"
        for item in report["items"]
    )
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "entry",
    [
        "${DEPLOYMENT_FILE}",
        "['${DEPLOYMENT_FILE}']",
        "\n      - path: ${DEPLOYMENT_FILE}\n        required: false",
    ],
)
def test_reviews_when_compose_env_file_uses_interpolation(
    tmp_path: Path, entry: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given runtime-selected Compose input and an already upgraded dependency.
    (tmp_path / "compose.yml").write_text(f"services:\n  app:\n    env_file: {entry}\n")
    (tmp_path / "app.py").write_text(CLIENT.replace(".create()", ".v0.create()"))
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When strict migration cannot resolve the selected file.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then unknown environment input cannot pass silently.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["env-api-url"]


@pytest.mark.parametrize("filename", ["deployment.yaml", ".env.example"])
def test_refuses_when_compose_loaded_file_has_an_otherwise_ignored_name(
    tmp_path: Path, filename: str
) -> None:
    # Given a dotenv-format input whose filename would skip or misclassify it.
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "compose.yml").write_text(
        f"services:\n  app:\n    env_file: {filename}\n"
    )
    (tmp_path / filename).write_text("DAYTONA_API_URL=https://self.example/api\n")

    # When Compose establishes the input's actual loader role.
    status = main([str(tmp_path), "--force", "--yes"])

    # Then name-based classification cannot hide the custom endpoint.
    assert status == 2
    assert (tmp_path / "app.py").read_text() == CLIENT


def test_ignores_env_file_text_when_it_is_a_comment_or_block_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given deployment text rather than a Compose loader declaration.
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "compose.yml").write_text(
        "services:\n  app:\n    # env_file: ${FILE}\n"
        "    command: |\n      env_file: ${FILE}\n"
    )

    # When strict preflight inspects actual YAML keys only.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then inert text does not invent a loader requiring review.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
