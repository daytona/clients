# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook environment setters use the same transactional safety as Python writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main

FIXTURE = Path(__file__).parent / "fixtures/notebook-environment/input.ipynb"
CUSTOM = "https://selfhost.example/api"
FORMS = [
    "%env {name}={value}",
    "%env {name} {value}",
    "%set_env {name}={value}",
    "%set_env {name} {value}",
    "!export {name}={value}",
]


def notebook_with_magic(command: str) -> str:
    return FIXTURE.read_text().replace(
        "%env DAYTONA_API_URL=" + CUSTOM, json.dumps(command)[1:-1]
    )


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("name", ["DAYTONA_API_URL", "DAYTONA_SERVER_URL"])
@pytest.mark.parametrize("drop", [False, True])
def test_refusal_when_notebook_magic_sets_custom_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], form: str, name: str, drop: bool
) -> None:
    # Given a real notebook fixture plus an otherwise migratable dependency.
    source = notebook_with_magic(form.format(name=name, value=CUSTOM))
    notebook = tmp_path / "example.ipynb"
    notebook.write_text(source)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.224.0\n")
    # When the writing CLI sees the setter, with or without the Cloud assertion.
    status = main(
        [str(tmp_path), "--json", "--force", "--yes"]
        + (["--drop-api-url"] if drop else [])
    )
    # Then refusal is atomic, or the override leaves a manual environment item.
    report = json.loads(capsys.readouterr().out)
    assert status == (0 if drop else 2)
    assert [item["kind"] for item in report["items"]] == (
        ["env-api-url"] if drop else ["env-api-url", "custom-api-url"]
    )
    assert all(
        item["file"] == "example.ipynb#cell=2" and item["line"] == 1
        for item in report["items"]
    )
    assert CUSTOM not in json.dumps(report)
    if drop:
        assert "client.v0.list()" in notebook.read_text()
        assert form.format(name=name, value=CUSTOM) in notebook.read_text()
    else:
        assert report["changedFiles"] == []
        assert notebook.read_text() == source
        assert requirements.read_text() == "daytona==0.224.0\n"


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize(
    "value",
    [
        "https://app.daytona.io/api",
        "'https://app.daytona.io/api'",
        '"https://app.daytona.io/api"',
        "$endpoint",
        "{endpoint}",
        "https://{host}/api",
    ],
)
def test_manual_only_when_magic_value_is_cloud_or_interpolated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], form: str, value: str
) -> None:
    # Given a Cloud literal or IPython/shell interpolation, not a proven custom URL.
    (tmp_path / "example.ipynb").write_text(
        notebook_with_magic(form.format(name="DAYTONA_API_URL", value=value))
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When preflighting through the CLI.
    status = main([str(tmp_path), "--json", "--dry"])
    # Then the setting needs review without a custom-URL refusal.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [item["kind"] for item in report["items"]] == ["env-api-url"]


@pytest.mark.parametrize(
    "line",
    [
        "%env",
        "%env DAYTONA_API_URL",
        "%env OTHER=value",
        "# %env DAYTONA_API_URL=" + CUSTOM,
        'text = """\n%env DAYTONA_API_URL=' + CUSTOM + '\n"""',
        "%%bash\n%env DAYTONA_API_URL=" + CUSTOM,
    ],
)
def test_ignores_when_magic_is_read_unrelated_or_not_executable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    # Given a separate setup cell that cannot write the Python kernel environment.
    raw = json.loads(FIXTURE.read_text())
    raw["cells"][1]["source"].pop(0)
    raw["cells"].insert(1, {"cell_type": "code", "source": [line + "\n"]})
    (tmp_path / "example.ipynb").write_text(json.dumps(raw))
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When scanning the notebook.
    status = main([str(tmp_path), "--json", "--dry"])
    # Then no environment write is invented.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
