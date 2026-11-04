# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Environment URL safety through the real project preflight and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.config_messages import ENV_CUSTOM_URL
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight

SOURCE = "from daytona import Daytona\nd = Daytona()\nd.create()\n"
CUSTOM = "https://self-host.example/api"
CLOUD = "https://app.daytona.io/api"
DEPLOYMENTS = [
    (".env.local", f"export DAYTONA_API_URL='{CUSTOM}'\n"),
    (
        "compose.yaml",
        f"services:\n  app:\n    environment:\n      DAYTONA_API_URL: {CUSTOM}\n",
    ),
    ("docker-compose.yml", f"environment:\n  - DAYTONA_SERVER_URL={CUSTOM}\n"),
    ("k8s/app.yaml", f"env:\n  - name: DAYTONA_API_URL\n    value: '{CUSTOM}'\n"),
    ("helm/values.yml", f'DAYTONA_SERVER_URL: "{CUSTOM}" # endpoint\n'),
    (".github/workflows/test.yml", f"env:\n  DAYTONA_API_URL: {CUSTOM}\n"),
    ("Dockerfile", f"ENV DAYTONA_API_URL={CUSTOM}\n"),
    ("Dockerfile.legacy", f"ENV DAYTONA_API_URL {CUSTOM}\n"),
    ("compose.yml", f'environment: {{"DAYTONA_API_URL": "{CUSTOM}"}}\n'),
    ("k8s/flow.yml", f'env:\n  - {{name: DAYTONA_API_URL, value: "{CUSTOM}"}}\n'),
]
WRITES = [
    f'import os\nos.environ["DAYTONA_API_URL"] = "{CUSTOM}"\n',
    f'import os\nos.environ.setdefault("DAYTONA_SERVER_URL", "{CUSTOM}")\n',
    f'import os\nos.putenv("DAYTONA_API_URL", "{CUSTOM}")\n',
    f'import os as system\nsystem.environ["DAYTONA_SERVER_URL"] = "{CUSTOM}"\n',
    f'from os import environ as env\nenv["DAYTONA_API_URL"] = "{CUSTOM}"\n',
    f'from os import putenv as put\nput("DAYTONA_SERVER_URL", "{CUSTOM}")\n',
    f'import os\nenv = os.environ\nenv.setdefault("DAYTONA_API_URL", "{CUSTOM}")\n',
    f'import os\nput = os.putenv\nput("DAYTONA_API_URL", "{CUSTOM}")\n',
    f'import os\ndef configure():\n os.environ["DAYTONA_API_URL"] = "{CUSTOM}"\n',
    f'import os\nos.environ["DAYTONA_API_URL"]: str = "{CUSTOM}"\n',
    f'import os\nos.environ.setdefault("DAYTONA_API_URL", default="{CUSTOM}")\n',
    f'import os\nos.environ.setdefault(key="DAYTONA_API_URL", default="{CUSTOM}")\n',
    f'import os\nos.environ.update({{"DAYTONA_API_URL": "{CUSTOM}"}})\n',
    f'import os\nos.environ.update(DAYTONA_SERVER_URL="{CUSTOM}")\n',
    f'from os import environ as env\nenv.update({{"DAYTONA_API_URL": "{CUSTOM}"}})\n',
    f'import os\nos.environ |= {{"DAYTONA_API_URL": "{CUSTOM}"}}\n',
    f'from os import environ as env\nenv |= {{"DAYTONA_SERVER_URL": "{CUSTOM}"}}\n',
]


@pytest.fixture(autouse=True)
def declared_dependency(tmp_path: Path) -> None:
    """Keep dependency-guard diagnostics outside these environment fixtures."""
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")


@pytest.mark.parametrize(
    "deployment", DEPLOYMENTS + [("env.py", write) for write in WRITES]
)
def test_refuses_before_writing_when_deployment_has_custom_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], deployment: tuple[str, str]
) -> None:
    # Given an otherwise migratable project with a visible self-hosted endpoint.
    relative, environment = deployment
    app = tmp_path / "app.py"
    app.write_text(SOURCE)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.120.0\n")
    deployment_file = tmp_path / relative
    deployment_file.parent.mkdir(parents=True, exist_ok=True)
    deployment_file.write_text(environment)
    # When the real CLI is allowed to write.
    code = main([str(tmp_path), "--json", "--yes"])
    # Then refusal is transactional and never exposes the endpoint value.
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert report["refused"]["message"] == ENV_CUSTOM_URL + " No files were written."
    assert report["changedFiles"] == []
    assert CUSTOM not in json.dumps(report)
    assert app.read_text() == SOURCE
    assert requirements.read_text() == "daytona==0.120.0\n"
    assert deployment_file.read_text() == environment


@pytest.mark.parametrize("write", WRITES)
def test_refuses_when_python_writes_custom_environment_url(
    tmp_path: Path, write: str
) -> None:
    # Given an actual os environment write, including stable aliases.
    (tmp_path / "app.py").write_text(write + SOURCE)
    # When the project is preflighted.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the same known-custom refusal accompanies the environment report.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]
    assert result.items[1]["message"] == ENV_CUSTOM_URL
    assert result.items[0]["line"] > 0
    assert result.items[0]["column"] > 0


@pytest.mark.parametrize(
    ("relative", "environment"), DEPLOYMENTS + [("env.py", write) for write in WRITES]
)
def test_reports_without_refusal_when_drop_api_url_asserted(
    tmp_path: Path, relative: str, environment: str
) -> None:
    # Given an explicit Cloud assertion despite a visible environment override.
    (tmp_path / "app.py").write_text(SOURCE)
    file = tmp_path / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(environment)
    # When that assertion is passed to preflight.
    result = preflight(tmp_path, TransformOptions(load_mapping(), drop_api_url=True))
    # Then the existing manual environment report remains and no environment is edited.
    assert [item["kind"] for item in result.items] == ["env-api-url"]
    assert all(change.file.relative != relative for change in result.changes)


@pytest.mark.parametrize(
    "value",
    [
        CLOUD,
        CLOUD + "/",
        "${CUSTOM_API_URL}",
        "${{ secrets.API_URL }}",
        "https://${CUSTOM_HOST}/api",
    ],
)
@pytest.mark.parametrize("relative", [".env", "compose.yaml"])
def test_reports_only_when_environment_value_is_cloud_or_dynamic(
    tmp_path: Path, value: str, relative: str
) -> None:
    # Given a Cloud URL or a value that cannot be proven statically.
    (tmp_path / "app.py").write_text(SOURCE)
    (tmp_path / relative).write_text(f'DAYTONA_API_URL="{value}"\n')
    # When preflight inspects it without an assertion flag.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then only manual review is required.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


@pytest.mark.parametrize(
    "value", [repr(CLOUD), repr(CLOUD + "/"), "endpoint", 'f"https://{host}/api"']
)
@pytest.mark.parametrize(
    "write",
    [
        'os.environ["DAYTONA_API_URL"] = VALUE',
        'os.environ.setdefault("DAYTONA_SERVER_URL", VALUE)',
        'os.putenv("DAYTONA_API_URL", VALUE)',
    ],
)
def test_reports_only_when_python_environment_value_is_cloud_or_dynamic(
    tmp_path: Path, value: str, write: str
) -> None:
    # Given a real write whose endpoint is Cloud or not a literal.
    (tmp_path / "app.py").write_text(
        "import os\n" + write.replace("VALUE", value) + "\n" + SOURCE
    )
    # When preflight inspects it.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the write still gets an environment item, without refusing migration.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


@pytest.mark.parametrize(
    "source",
    [
        f'def configure(os):\n os.environ["DAYTONA_API_URL"] = "{CUSTOM}"\n',
        f'import os\nos = other\nos.putenv("DAYTONA_API_URL", "{CUSTOM}")\n',
        f'import os\ndef configure():\n os.environ["DAYTONA_API_URL"] = "{CUSTOM}"\n os = other\n',
        f'from os import environ as env\ndef configure(env):\n env.setdefault("DAYTONA_API_URL", "{CUSTOM}")\n',
        f'import os\nvalue = os.environ["DAYTONA_API_URL"]\n# os.putenv("DAYTONA_API_URL", "{CUSTOM}")\n',
        f'import os\ntext = \'os.environ["DAYTONA_API_URL"] = "{CUSTOM}"\'\n',
    ],
)
def test_ignores_when_os_is_shadowed_or_environment_is_not_written(
    tmp_path: Path, source: str
) -> None:
    # Given unrelated objects, reads, comments, or strings that resemble writes.
    (tmp_path / "app.py").write_text(source + SOURCE)
    # When preflight resolves the actual bindings.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then these are not environment writes.
    assert not result.items


@pytest.mark.parametrize(
    "source",
    [
        "env:\n - name: DAYTONA_API_URL\n   valueFrom:\n     secretKeyRef:\n       name: endpoint\n       key: url\n",
        f"env:\n - name: DAYTONA_API_URL\n - name: OTHER\n   value: {CUSTOM}\n",
        "DAYTONA_API_URL: null\n",
        "DAYTONA_API_URL: *endpoint\n",
        "DAYTONA_API_URL: |\n  https://app.daytona.io/api\n",
    ],
)
def test_reports_only_when_deployment_has_indirect_value(
    tmp_path: Path, source: str
) -> None:
    # Given a historical scan hit without a literal URL assigned to this variable.
    (tmp_path / "app.py").write_text(SOURCE)
    file = tmp_path / "k8s" / "app.yml"
    file.parent.mkdir()
    file.write_text(source)
    # When deployment locations are scanned.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the existing report survives without inventing custom-URL evidence.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


@pytest.mark.parametrize("drop_api_url", [False, True])
def test_reports_cell_coordinates_when_notebook_sets_environment_url(
    tmp_path: Path, drop_api_url: bool
) -> None:
    # Given aliases across code cells, a markdown cell, and a masked pip magic.
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Setup"]},
            {"cell_type": "code", "source": ["import os as system\n"]},
            {
                "cell_type": "code",
                "source": [
                    "%pip install daytona\n",
                    f'system.environ["DAYTONA_API_URL"] = "{CUSTOM}"\n',
                    SOURCE,
                ],
            },
        ]
    }
    (tmp_path / "example.ipynb").write_text(json.dumps(notebook))
    # When the project scans the adapter's combined, masked Python source.
    result = preflight(
        tmp_path, TransformOptions(load_mapping(), drop_api_url=drop_api_url)
    )
    # Then environment reports use the same cell/local-line convention as transforms.
    assert [item["kind"] for item in result.items] == (
        ["env-api-url"] if drop_api_url else ["env-api-url", "custom-api-url"]
    )
    assert all(
        item["file"] == "example.ipynb#cell=3" and item["line"] == 2
        for item in result.items
    )


@pytest.mark.parametrize("value", ["self-host/api", "${HOST}/api"])
def test_refuses_when_python_sets_non_cloud_string_literal(
    tmp_path: Path, value: str
) -> None:
    # Given a literal string, not a Python expression or expanded shell variable.
    (tmp_path / "app.py").write_text(
        f"import os\nos.environ['DAYTONA_API_URL'] = {value!r}\n" + SOURCE
    )
    # When the project is preflighted.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then nonempty non-Cloud values require an explicit assertion.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]
