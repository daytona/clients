# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Endpoint defaults are evidence only at actual environment declarations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.environment import scan_environment


@pytest.mark.parametrize(
    "case",
    [
        (filename, operator, endpoint)
        for filename in [".env", "Dockerfile", "compose.yaml"]
        for operator in ["-", ":-", "+", ":+"]
        for endpoint in ["https://app.daytona.io/api", "https://custom/api"]
    ],
)
def test_classifies_default_when_endpoint_is_declared(
    tmp_path: Path,
    case: tuple[str, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a real deployment declaration using a runtime-selectable word.
    filename, operator, endpoint = case
    expression = "${DAYTONA_API_URL" + operator + endpoint + "}"
    declaration = {
        ".env": f"DAYTONA_API_URL={expression}\n",
        "Dockerfile": f"ENV DAYTONA_API_URL={expression}\n",
        "compose.yaml": f"services: {{app: {{environment: {{DAYTONA_API_URL: {expression}}}}}}}\n",
    }[filename]
    files = {
        "app.py": "from daytona import Daytona\nDaytona().create()\n",
        "requirements.txt": "daytona==0.224.0\n",
        filename: declaration,
    }
    for name, source in files.items():
        (tmp_path / name).write_text(source)
    # When the writing CLI evaluates literal default evidence.
    status = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then custom defaults refuse atomically; Cloud defaults only retain review.
    report = json.loads(capsys.readouterr().out)
    custom = endpoint == "https://custom/api"
    assert status == (2 if custom else 0)
    assert [item["kind"] for item in report["items"]] == (
        ["env-api-url", "custom-api-url"] if custom else ["env-api-url"]
    )
    if custom:
        assert {name: (tmp_path / name).read_text() for name in files} == files
    else:
        assert "Daytona().v0.create()" in (tmp_path / "app.py").read_text()


@pytest.mark.parametrize("filename", [".env", "Dockerfile", "compose.yaml"])
@pytest.mark.parametrize(
    "expression",
    [
        "${DAYTONA_API_URL}",
        "${DAYTONA_API_URL:?https://custom/api}",
        "${DAYTONA_API_URL?https://custom/api}",
        "${DAYTONA_API_URL:-${OTHER}}",
    ],
)
def test_reviews_when_endpoint_expression_is_unresolved(
    filename: str, expression: str
) -> None:
    # Given an actual declaration without a provable endpoint word.
    source = (
        ("ENV " if filename == "Dockerfile" else "") + "DAYTONA_API_URL=" + expression
    )
    # When scanning the environment declaration.
    writes = scan_environment(source, filename)
    # Then error messages and unresolved references are never custom URL evidence.
    assert [item["kind"] for write in writes for item in write.items(False)] == [
        "env-api-url"
    ]


@pytest.mark.parametrize("filename", [".env", "Dockerfile", "compose.yaml"])
@pytest.mark.parametrize("operator", ["", "-", ":-", "?", ":?", "+", ":+"])
def test_ignores_reference_when_another_variable_is_declared(
    filename: str, operator: str
) -> None:
    # Given another setting whose value merely references the Daytona variable.
    expression = "${DAYTONA_API_URL" + operator + "https://custom/api}"
    source = ("ENV " if filename == "Dockerfile" else "") + "API=" + expression
    # When scanning declarations rather than names embedded in values.
    writes = scan_environment(source, filename)
    # Then the unrelated variable produces no review or refusal evidence.
    assert writes == ()
