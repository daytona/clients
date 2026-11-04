# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Environment unions and templates obey project preflight and CLI safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight

SOURCE = "from daytona import Daytona\nd = Daytona()\nd.create()\n"
CUSTOM = "https://self-host.example/api"


@pytest.mark.parametrize("drop_api_url", [False, True])
@pytest.mark.parametrize(
    "write",
    [
        f"{receiver} |= {{{variable!r}: {CUSTOM!r}}}\n"
        for receiver in ("os.environ", "env")
        for variable in ("DAYTONA_API_URL", "DAYTONA_SERVER_URL")
    ],
)
def test_bulk_union_respects_refusal_when_environment_is_bound(
    tmp_path: Path, write: str, drop_api_url: bool
) -> None:
    # Given a migratable project with a literal bulk environment write.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    environment = "import os\nenv = os.environ\n" + write
    (tmp_path / "app.py").write_text(environment + SOURCE)
    # When preflight applies the caller's Cloud assertion choice.
    result = preflight(
        tmp_path, TransformOptions(load_mapping(), drop_api_url=drop_api_url)
    )
    # Then bulk writes obey exactly the single-write refusal/manual-review policy.
    assert [item["kind"] for item in result.items] == (
        ["env-api-url"] if drop_api_url else ["env-api-url", "custom-api-url"]
    )


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template"])
def test_ignores_environment_when_file_is_a_template(tmp_path: Path, name: str) -> None:
    # Given a template with placeholders and a real dependency to migrate.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    directory = tmp_path / "nested"
    directory.mkdir()
    (directory / name).write_text(
        "DAYTONA_API_URL=your-url-here\nDAYTONA_SERVER_URL=localhost\n"
    )
    # When the entire project is scanned.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then templates are ignored entirely, not even manual-review items.
    assert result.items == ()


@pytest.mark.parametrize(
    "source",
    [
        f"# DAYTONA_API_URL={CUSTOM}\n",
        f"image: app # DAYTONA_SERVER_URL={CUSTOM}\n",
        "DAYTONA_API_URL=\n",
        'DAYTONA_API_URL=""\n',
        "DAYTONA_SERVER_URL=''\n",
        "DAYTONA_API_URL= # unset\n",
        'ENV DAYTONA_API_URL ""\n',
        "env:\n - name: DAYTONA_API_URL\n   value: # unset\n",
    ],
)
def test_ignores_environment_when_comment_or_empty(tmp_path: Path, source: str) -> None:
    # Given comments or unset values in a recognized deployment file.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / ".env").write_text(source)
    # When the project is scanned.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then inactive settings neither refuse nor require review.
    assert result.items == ()


def test_ignores_environment_when_python_writes_empty_value(tmp_path: Path) -> None:
    # Given a real Python environment setter with no endpoint value.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text('import os\nos.environ["DAYTONA_API_URL"] = ""\n')
    # When Python binding-aware scanning visits the write.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then empty values are ignored just like empty deployment settings.
    assert result.items == ()


@pytest.mark.parametrize(
    "value", ["HTTPS://APP.DAYTONA.IO/api", "https://App.Daytona.IO/api/"]
)
@pytest.mark.parametrize("relative", [".env", "env.py"])
def test_reports_cloud_when_scheme_and_host_have_mixed_case(
    tmp_path: Path, value: str, relative: str
) -> None:
    # Given the Cloud endpoint with a case-insensitive scheme and hostname.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    source = (
        f'import os\nos.environ["DAYTONA_API_URL"] = {value!r}\n'
        if relative.endswith(".py")
        else f"DAYTONA_API_URL={value}\n"
    )
    (tmp_path / relative).write_text(source)
    # When preflight checks the endpoint without an override.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then only manual review remains; no custom endpoint was observed.
    assert [item["kind"] for item in result.items] == ["env-api-url"]


@pytest.mark.parametrize("relative", [".env.test", ".env", "env.py"])
def test_cli_preserves_project_when_environment_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], relative: str
) -> None:
    # Given a literal custom endpoint in an active env file or source write.
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.120.0\n")
    app = tmp_path / "app.py"
    app.write_text(SOURCE)
    environment = (
        f'import os\nos.environ["DAYTONA_API_URL"] = {CUSTOM!r}\n'
        if relative.endswith(".py")
        else f"DAYTONA_API_URL={CUSTOM}\n"
    )
    file = tmp_path / relative
    file.write_text(environment)
    # When the real CLI attempts an upgrade without asserting Cloud.
    code = main([str(tmp_path), "--yes", "--json"])
    report = json.loads(capsys.readouterr().out)
    # Then refusal preserves every file and gives the supported recovery flag.
    assert code == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert (
        "If it points at Daytona Cloud, rerun with --drop-api-url."
        in report["refused"]["message"]
    )
    assert report["changedFiles"] == []
    assert app.read_text() == SOURCE
    assert requirements.read_text() == "daytona==0.120.0\n"
    assert file.read_text() == environment


@pytest.mark.parametrize("receiver", ["os.environ", "env"])
def test_refuses_later_write_when_environment_was_updated_in_place(
    tmp_path: Path, receiver: str
) -> None:
    # Given a real environment identity that survives a harmless in-place union.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(
        f'import os\nenv = os.environ\n{receiver} |= {{"OTHER": "ok"}}\n'
        f'{receiver}["DAYTONA_API_URL"] = {CUSTOM!r}\n'
    )
    # When preflight scans a later write through the same receiver.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then in-place union must not erase the environment identity.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize(
    "source",
    [
        f'def configure(os):\n os.environ |= {{"DAYTONA_API_URL": {CUSTOM!r}}}\n',
        f'import os\nenv = other\nenv |= {{"DAYTONA_API_URL": {CUSTOM!r}}}\n',
    ],
)
def test_ignores_union_when_receiver_is_unrelated(tmp_path: Path, source: str) -> None:
    # Given a same-shaped operation without a proven os.environ binding.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text(source)
    # When preflight applies lexical environment resolution.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then an unrelated mapping cannot cause a spurious refusal.
    assert result.items == ()


@pytest.mark.parametrize(
    "value", ["HTTPS://APP.DAYTONA.IO/API", "https://app.daytona.io.evil/api"]
)
def test_refuses_environment_when_only_cloud_host_or_path_resembles_cloud(
    tmp_path: Path, value: str
) -> None:
    # Given a lookalike host or a different case-sensitive path.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / ".env").write_text(f"DAYTONA_API_URL={value}\n")
    # When recognizing the Cloud endpoint.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then hostname normalization does not broaden the accepted endpoint.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize("relative", [".env.test", "env.py"])
def test_cli_upgrades_when_environment_is_explicitly_acknowledged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], relative: str
) -> None:
    # Given migratable code and an environment URL that the caller asserts is Cloud.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    app = tmp_path / "app.py"
    app.write_text(SOURCE)
    environment = (
        f'import os\nos.environ |= {{"DAYTONA_SERVER_URL": {CUSTOM!r}}}\n'
        if relative.endswith(".py")
        else f"DAYTONA_API_URL={CUSTOM}\n"
    )
    file = tmp_path / relative
    file.write_text(environment)
    # When the real CLI receives the assertion flag.
    code = main([str(tmp_path), "--yes", "--json", "--drop-api-url"])
    report = json.loads(capsys.readouterr().out)
    # Then code migrates while the environment stays byte-identical and reportable.
    assert code == 0
    assert "refused" not in report
    assert [item["kind"] for item in report["items"]] == ["env-api-url"]
    assert "d.v0.create()" in app.read_text()
    assert file.read_text() == environment
