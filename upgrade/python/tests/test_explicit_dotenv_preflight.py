# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Explicit environment inputs receive the same transactional URL preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main

CLIENT = "from daytona import Daytona\nclient = Daytona()\nclient.create()\n"
LOADERS = [
    'from dotenv import load_dotenv\nload_dotenv("deployment.conf", override=True)\n',
    'from dotenv import load_dotenv as load\nload(dotenv_path="deployment.conf")\n',
    'import dotenv\ndotenv.load_dotenv("deployment.conf")\n',
    'from dotenv import load_dotenv, find_dotenv\nload_dotenv(find_dotenv("deployment.conf"))\n',
    'from pathlib import Path\nfrom dotenv import load_dotenv\nload_dotenv(Path(__file__).parent / "deployment.conf")\n',
    'import os\nfrom dotenv import load_dotenv\nload_dotenv(os.path.join(os.path.dirname(__file__), "deployment.conf"))\n',
    'import os\nfrom dotenv import dotenv_values\nos.environ.update(dotenv_values("deployment.conf"))\n',
    'from dotenv import dotenv_values\nfrom daytona import DaytonaConfig\nvalues = dotenv_values("deployment.conf")\nconfig = DaytonaConfig(api_url=values["DAYTONA_API_URL"])\n',
    'from pydantic_settings import BaseSettings, SettingsConfigDict\nclass Settings(BaseSettings):\n    model_config = SettingsConfigDict(env_file="deployment.conf")\n    daytona_api_url: str = ""\nsettings = Settings()\n',
    'from pydantic import BaseSettings\nclass Settings(BaseSettings):\n    daytona_api_url: str = ""\n    class Config:\n        env_file = "deployment.conf"\nsettings = Settings()\n',
]


@pytest.mark.parametrize(
    "loader,location",
    [
        (loader, location)
        for loader in LOADERS
        for location in ("root", "source")
        if location != "root" or "__file__" not in loader
    ],
)
def test_refuses_without_writes_when_literal_loader_has_custom_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], loader: str, location: str
) -> None:
    # Given a custom endpoint outside the name-based environment-file discovery.
    directory = tmp_path / "src"
    directory.mkdir()
    source = loader + CLIENT
    app = directory / "app.py"
    app.write_text(source)
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("daytona==0.224.0\n")
    environment = (tmp_path if location == "root" else directory) / "deployment.conf"
    environment.write_text("DAYTONA_API_URL=https://self.example/api\n")

    # When a strict migration would otherwise rewrite the client and dependency.
    status = main([str(tmp_path), "--yes", "--force", "--strict", "--json"])

    # Then refusal precedes every write, including the dependency bump.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["changedFiles"] == []
    assert app.read_text() == source
    assert requirement.read_text() == "daytona==0.224.0\n"
    assert environment.read_text() == "DAYTONA_API_URL=https://self.example/api\n"


@pytest.mark.parametrize(
    "loader",
    [
        "from dotenv import load_dotenv\nload_dotenv(selected_path)\n",
        "from dotenv import dotenv_values\nvalues = dotenv_values(selected_path)\n",
        "from dotenv import load_dotenv\nload_dotenv(stream=stream)\n",
        "from pydantic_settings import SettingsConfigDict\nmodel_config = SettingsConfigDict(env_file=selected_path)\n",
    ],
)
def test_requires_review_when_environment_input_is_dynamic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], loader: str
) -> None:
    # Given a loader whose actual input cannot be inspected statically.
    (tmp_path / "app.py").write_text(loader + CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When strict preflight resolves its binding but not its filename.
    status = main([str(tmp_path), "--yes", "--force", "--strict", "--json"])

    # Then the unresolved environment source cannot silently pass strict mode.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert "env-api-url" in [item["kind"] for item in report["items"]]


def test_migrates_normally_when_loaded_file_has_no_endpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a literal loader with only unrelated configuration.
    source = LOADERS[0] + CLIENT
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "deployment.conf").write_text("OTHER_SETTING=value\n")
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When the real CLI inspects the file before migration.
    status = main([str(tmp_path), "--yes", "--force", "--strict", "--json"])

    # Then safe literal input introduces no review debt or refusal.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert app.read_text() == source.replace("client.create()", "client.v0.create()")
