# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Computed environment writes preserve refusal and manual-review evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "source",
    [
        f"import os\n{header}{indent}key = {variable!r}\n"
        f"{indent}url = 'https://self.host/api'\n{indent}{write}\n"
        for variable in ("DAYTONA_API_URL", "DAYTONA_SERVER_URL")
        for header, indent in (("", ""), ("def configure():\n", "    "))
        for write in (
            "os.environ.update({key: url})",
            "os.environ[key] = url",
            "os.environ.setdefault(key, url)",
            "os.putenv(key, url)",
            "os.environ.__setitem__(key, url)",
            "os.environ |= {key: url}",
        )
    ],
)
@pytest.mark.parametrize("drop_api_url", [False, True])
def test_reports_constant_key_when_environment_write_is_indirect(
    tmp_path: Path, source: str, drop_api_url: bool
) -> None:
    # Given an environment setter using module or local string bindings.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(source)
    # When the project is preflighted using the caller's Cloud assertion.
    result = preflight(
        tmp_path, TransformOptions(load_mapping(), drop_api_url=drop_api_url)
    )
    # Then indirect writes have exactly the same policy as literal writes.
    assert [item["kind"] for item in result.items] == (
        ["env-api-url"] if drop_api_url else ["env-api-url", "custom-api-url"]
    )


@pytest.mark.parametrize(
    "write",
    [
        "os.environ[key] = 'https://self.host/api'",
        "os.environ.update({key: 'https://self.host/api'})",
        "os.environ.setdefault(key, 'https://self.host/api')",
        "os.putenv(key, 'https://self.host/api')",
        "os.environ.__setitem__(key, 'https://self.host/api')",
        "os.environ |= {key: 'https://self.host/api'}",
        "os.environ[key], other = values",
        "[os.environ[key]] = values",
        "for os.environ[key] in values: pass",
    ],
)
def test_reports_manual_review_when_environment_key_is_unknown(
    tmp_path: Path, write: str
) -> None:
    # Given an environment write whose key comes from an unknown caller.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(f"import os\ndef configure(key):\n    {write}\n")
    # When the scan cannot classify which variable is being written.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then it reports potential endpoint changes without inventing a custom URL.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


@pytest.mark.parametrize(
    "body",
    [
        "key = 'OTHER'\nos.environ.update({key: 'https://self.host/api'})",
        "key = 'OTHER'\nos.environ[key] = 'https://self.host/api'",
        "key = 'DAYTONA_API_URL'\nvalue = os.environ[key]",
        "key = 'DAYTONA_API_URL'\n# os.environ[key] = 'https://self.host/api'",
        "os.environ[key] = ''",
    ],
)
def test_ignores_indirect_key_when_not_an_endpoint_write(
    tmp_path: Path, body: str
) -> None:
    # Given unrelated variable writes or endpoint reads/comments.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text("import os\n" + body + "\n")
    # When source writes are scanned.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then only actual potential endpoint writes need review.
    assert result.items == ()


def test_reports_manual_review_when_endpoint_write_is_augmented(tmp_path: Path) -> None:
    # Given an endpoint whose new value depends on the running environment.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(
        "import os\nkey = 'DAYTONA_API_URL'\nos.environ[key] += '/api'\n"
    )
    # When the setter cannot prove the resulting URL.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the write remains reportable without a fabricated literal endpoint.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


def test_refuses_constant_key_when_module_binding_is_used_in_function(
    tmp_path: Path,
) -> None:
    # Given a module constant referenced by a local environment setter.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(
        "import os\nKEY = 'DAYTONA_API_URL'\ndef configure():\n"
        "    os.environ.update({KEY: 'https://self.host/api'})\n"
    )
    # When the function uses the enclosing literal binding.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the known endpoint is refused just like a module-level write.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize(
    "source",
    [
        "ENV DAYTONA_API_URL=https://app.daytona.io/api OTHER=x\n",
        "ENV OTHER=x DAYTONA_SERVER_URL=https://app.daytona.io/api\n",
        "ENV DAYTONA_API_URL \\\n https://app.daytona.io/api\n",
        "ENV DAYTONA_API_URL=https://app.daytona.io/api \\\n OTHER=x\n",
    ],
)
def test_accepts_cloud_when_docker_env_has_pairs_or_continuations(
    tmp_path: Path, source: str
) -> None:
    # Given a Cloud URL in a Docker ENV instruction.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "Dockerfile").write_text(source)
    # When deployment instructions are scanned as logical lines.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then Cloud remains report-only, never a custom-endpoint refusal.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


def test_cli_preserves_project_when_constant_key_sets_custom_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a migratable project with an indirect self-hosted environment write.
    requirement = tmp_path / "requirements.txt"
    requirement.write_text("daytona==0.120.0\n")
    app = tmp_path / "app.py"
    source = (
        "import os\nfrom daytona import Daytona\nKEY = 'DAYTONA_API_URL'\n"
        "os.environ.update({KEY: 'https://self.host/api'})\nDaytona().create()\n"
    )
    app.write_text(source)
    # When the actual CLI is invoked without a Cloud assertion.
    status = main([str(tmp_path), "--yes", "--json"])
    # Then the refusal keeps the complete project unchanged and exposes both items.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert [item["kind"] for item in report["items"]] == [
        "env-api-url",
        "custom-api-url",
    ]
    assert report["changedFiles"] == []
    assert app.read_text() == source
    assert requirement.read_text() == "daytona==0.120.0\n"
