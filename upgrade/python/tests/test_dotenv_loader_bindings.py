# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Loader preflight resolves real bindings and keeps external-file coordinates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_explicit_dotenv_preflight import CLIENT

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "loader",
    [
        'from dotenv import load_dotenv\nfilename = "settings.conf"\nload_dotenv(filename)\n',
        'from dotenv.main import load_dotenv as load\nload("settings.conf")\n',
        'from dotenv import find_dotenv as find, load_dotenv as load\nfilename = find(filename="settings.conf")\nload(filename)\n',
        'from pydantic_settings import SettingsConfigDict as Config\nmodel_config = Config(env_file=("missing.conf", "settings.conf"))\n',
        "from dotenv import load_dotenv\nload_dotenv(override=True)\n",
        "from dotenv import load_dotenv\nload_dotenv(dotenv_path=None, override=True)\n",
    ],
)
def test_refuses_when_loader_uses_literal_alias_or_default(
    tmp_path: Path, loader: str
) -> None:
    # Given literal filename aliases and a custom endpoint under their actual name.
    (tmp_path / "app.py").write_text(loader + CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    name = ".env" if "override=True" in loader else "settings.conf"
    (tmp_path / name).write_text("DAYTONA_API_URL=https://self.example/api\n")

    # When the bounded lexical resolver follows the loader's argument.
    result = preflight(tmp_path, TransformOptions(load_mapping()))

    # Then aliases do not bypass custom endpoint evidence.
    assert "custom-api-url" in [item["kind"] for item in result.items]


@pytest.mark.parametrize(
    "loader",
    [
        'def load_dotenv(path): pass\nload_dotenv("settings.conf")\n',
        'from dotenv import load_dotenv\nload_dotenv = lambda path: None\nload_dotenv("settings.conf")\n',
        'from dotenv import load_dotenv\ndef run(load_dotenv):\n    load_dotenv("settings.conf")\n',
    ],
)
def test_ignores_unproven_loader_when_spelling_is_unrelated(
    tmp_path: Path, loader: str
) -> None:
    # Given a foreign or shadowed function and an otherwise unselected config file.
    (tmp_path / "app.py").write_text(loader + CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "settings.conf").write_text(
        "DAYTONA_API_URL=https://self.example/api\n"
    )

    # When the scanner checks callee provenance instead of its spelling.
    result = preflight(tmp_path, TransformOptions(load_mapping()))

    # Then unrelated functions introduce no environment refusal or review debt.
    assert result.items == ()


def test_refuses_when_either_relative_location_contains_custom_url(
    tmp_path: Path,
) -> None:
    # Given a clean CWD-relative file but a custom endpoint next to the source.
    directory = tmp_path / "src"
    directory.mkdir()
    (directory / "app.py").write_text(
        'from dotenv import load_dotenv\nload_dotenv("settings.conf")\n' + CLIENT
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "settings.conf").write_text("OTHER=value\n")
    (directory / "settings.conf").write_text(
        "DAYTONA_API_URL=https://self.example/api\n"
    )

    # When both plausible runtime working directories exist.
    result = preflight(tmp_path, TransformOptions(load_mapping()))

    # Then finding the clean candidate first does not hide the custom candidate.
    assert [(item["file"], item["kind"]) for item in result.items] == [
        ("src/settings.conf", "env-api-url"),
        ("src/settings.conf", "custom-api-url"),
    ]


def test_keeps_environment_file_coordinates_when_notebook_loads_dotenv(
    tmp_path: Path,
) -> None:
    # Given a notebook loading an environment file rather than assigning its values.
    (tmp_path / "example.ipynb").write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": [
                            'from dotenv import load_dotenv\nload_dotenv("settings.conf")\n'
                            + CLIENT
                        ],
                    },
                ]
            }
        )
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "settings.conf").write_text(
        "DAYTONA_API_URL=https://self.example/api\n"
    )

    # When the notebook adapter maps source-local reports back to cells.
    result = preflight(tmp_path, TransformOptions(load_mapping()))

    # Then the endpoint evidence still points at the loaded file, not a code cell.
    assert [(item["file"], item["line"]) for item in result.items] == [
        ("settings.conf", 1),
        ("settings.conf", 1),
    ]
