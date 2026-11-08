# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Import roots and external receiver origins preserve bounded SDK evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from report_schema import validate_report

from daytona_upgrade.cli import main


@pytest.mark.parametrize("name", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize(
    "relative",
    [
        "myapp/integrations/{name}/__init__.py",
        "charts/{name}/values.yaml",
        "docs/{name}/README.md",
        "myapp/integrations/{name}/helpers.py",
    ],
)
def test_dry_succeeds_when_sdk_name_is_outside_import_roots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, relative: str
) -> None:
    # Given nested Python integration packages or non-package directories.
    local = tmp_path / relative.format(name=name)
    local.parent.mkdir(parents=True)
    local.write_text("")
    app = tmp_path / "app.py"
    source = "from daytona import Daytona\nDaytona().create()\n"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    # When previewing a real project upgrade.
    status = main([str(tmp_path), "--dry", "--json"])
    # Then upgrading proceeds, with shadowing review only for regular packages.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert "refused" not in report
    assert app.read_text() == source
    assert "requirements.txt" in report["changedFiles"]
    assert [
        item["kind"]
        for item in report["items"]
        if item["file"] == local.relative_to(tmp_path).as_posix()
    ] == (["private-import"] if local.name == "__init__.py" else [])
    if local.name == "__init__.py":
        assert report["items"][0]["file"] == local.relative_to(tmp_path).as_posix()
        assert (
            "only if its parent directory is on sys.path"
            in report["items"][0]["message"]
        )
    if local.suffix not in {".py", ".pyi"}:
        assert report["items"] == []
    validate_report(report)


@pytest.mark.parametrize("flag", ["--dry", "--print", "--force"])
@pytest.mark.parametrize("git_failure", ["dubious", "missing", "unexecutable"])
def test_scan_succeeds_when_git_cannot_answer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    git_failure: str,
) -> None:
    # Given a checkout Git cannot inspect because of an ownership error.
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    tools = tmp_path / "bin"
    tools.mkdir()
    if git_failure != "missing":
        git = tools / "git"
        git.write_text(
            "#!/bin/sh\nprintf '%s\\n' 'fatal: detected dubious ownership in repository' >&2\nexit 128\n"
        )
        git.chmod(0o755 if git_failure == "dubious" else 0o644)
    monkeypatch.setenv("PATH", str(tools))
    # When invoking a mode that skips Git safety, but still classifies paths.
    status = main([str(project), flag, "--json"])
    # Then Git lookup failure does not block the same migration plan.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert "refused" not in report
    assert report["changedFiles"] == ["requirements.txt"]
    assert report["items"] == []


def test_help_matches_when_suggestion_removal_is_documented(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given the public CLI help surface.
    # When requesting help without touching a project.
    status = main(["--help"])
    # Then the removal description matches the shared CLI contract exactly.
    help_text = capsys.readouterr().out
    description = help_text.split("\n  --remove-suggestions", 1)[1].split("\n  --", 1)[
        0
    ]
    assert status == 0
    assert " ".join(description.split()) == "Remove daytona-v2: suggestion comments"


@pytest.mark.parametrize(
    "setup",
    [
        "import redis\nredis_client = redis.Redis()",
        "import redis as cache\nredis_client = cache.Redis()",
        'import boto3\nredis_client = boto3.client("s3")',
        "from external import Factory\nredis_client = Factory()",
        "from external import Factory as Make\nredis_client = Make()",
        "from external import nested\nredis_client = nested.Factory()",
        "import external.nested\nredis_client = external.nested.Factory()",
    ],
)
def test_strict_succeeds_when_receiver_is_built_by_external_callable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], setup: str
) -> None:
    # Given external constructors in a Daytona-using file with heuristic names.
    source = f"from daytona import Daytona\n{setup}\nredis_client.get(k)\nredis_client.delete(k)\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    # When running the real CLI under strict mode.
    status = main([str(tmp_path), "--dry", "--strict", "--json"])
    # Then external calls produce neither manual items nor code edits.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert report["changedFiles"] == ["requirements.txt"]
    assert app.read_text() == source


@pytest.mark.parametrize("module", ["redis", "external"])
@pytest.mark.parametrize("import_form", ["module", "from"])
def test_strict_stays_clean_when_project_local_factory_has_no_daytona_link(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    module: str,
    import_form: str,
) -> None:
    # Given a project-owned factory whose result is unresolved.
    (tmp_path / f"{module}.py").write_text("def Factory(): return unknown()\n")
    setup = (
        f"import {module}\nredis_client = {module}.Factory()"
        if import_form == "module"
        else f"from {module} import Factory\nredis_client = Factory()"
    )
    (tmp_path / "app.py").write_text(
        f"from daytona import Daytona\n{setup}\nredis_client.get(k)\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    # When applying the same strict scan to a local callable.
    status = main([str(tmp_path), "--dry", "--strict", "--json"])
    # Then local ownership alone supplies no Daytona provenance for its result.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert report["changedFiles"] == ["requirements.txt"]
