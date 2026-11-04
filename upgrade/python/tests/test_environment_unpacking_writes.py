# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Environment writes retain their value through unpacking and keyword calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "write",
    [
        'os.environ["DAYTONA_API_URL"], x = "https://custom", 1',
        '[x, os.environ["DAYTONA_API_URL"]] = [1, "https://custom"]',
        '(x, [y, os.environ["DAYTONA_API_URL"]]) = (1, [2, "https://custom"])',
        '[os.environ["DAYTONA_SERVER_URL"], x] = ("https://custom", 1)',
        'os.environ["DAYTONA_API_URL"], x = alias = "https://custom", 1',
        'os.environ.__setitem__(key="DAYTONA_API_URL", value="https://custom")',
        'os.environ.__setitem__(value="https://custom", key="DAYTONA_SERVER_URL")',
        'os.environ.__setitem__("DAYTONA_API_URL", value="https://custom")',
        'os.environ.setdefault(key="DAYTONA_API_URL", default="https://custom")',
    ],
)
def test_cli_refuses_custom_url_when_write_preserves_literal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], write: str
) -> None:
    # Given a real migration with an indirectly assigned custom endpoint.
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("daytona==0.120.0\n")
    app = tmp_path / "app.py"
    source = f"import os\nfrom daytona import Daytona\n{write}\nDaytona().create()\n"
    app.write_text(source)
    # When invoking the CLI without a Cloud assertion.
    status = main([str(tmp_path), "--json", "--yes"])
    # Then refusal prevents all migration writes.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert [item["kind"] for item in report["items"]] == [
        "env-api-url",
        "custom-api-url",
    ]
    assert app.read_text() == source
    assert manifest.read_text() == "daytona==0.120.0\n"


@pytest.mark.parametrize(
    "write",
    [
        'os.environ["DAYTONA_API_URL"], x = values',
        'os.environ["DAYTONA_API_URL"], x = ("https://custom",)',
        'os.environ["DAYTONA_API_URL"], *x = values',
        '[os.environ["DAYTONA_API_URL"], x] = [*values, "https://custom"]',
        'x, os.environ["DAYTONA_API_URL"] = [1, endpoint]',
        'os.environ.__setitem__(key="DAYTONA_API_URL", value=endpoint)',
        'os.environ.__setitem__(key=key, value="https://custom")',
        'os.environ.__setitem__(key="DAYTONA_API_URL", value="https://app.daytona.io/api")',
        'os.environ["DAYTONA_API_URL"], x = "https://app.daytona.io/api", "https://custom"',
    ],
)
def test_reports_review_when_environment_write_is_unresolved_or_cloud(
    tmp_path: Path, write: str
) -> None:
    # Given an endpoint write without a proven custom literal.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(f"import os\n{write}\n")
    # When inspecting the environment.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then it requires review without fabricating a custom URL.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


@pytest.mark.parametrize(
    "write",
    [
        'os.environ["OTHER"], x = "https://custom", 1',
        'os.environ["DAYTONA_API_URL"], x = "", "https://custom"',
        'os.environ.__setitem__(key="OTHER", value="https://custom")',
        'os.environ.__setitem__(key="DAYTONA_API_URL", value="")',
    ],
)
def test_ignores_write_when_endpoint_is_empty_or_unrelated(
    tmp_path: Path, write: str
) -> None:
    # Given an unrelated or empty environment write.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(f"import os\n{write}\n")
    # When inspecting the environment.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then it does not manufacture an endpoint finding.
    assert result.items == ()
