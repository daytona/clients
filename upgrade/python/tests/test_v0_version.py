# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The lockstep dependency target and the project's 0.x version check."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import jsonschema
import pytest

from daytona_upgrade import cli
from daytona_upgrade.cli import main
from daytona_upgrade.models import load_mapping
from daytona_upgrade.sdk_target import target_specifier
from daytona_upgrade.v0_version import (
    allowed_newer_message,
    newer_patch_message,
    newer_refusal,
    older_message,
    unknown_message,
)

SOURCE = "from daytona import Daytona\nd = Daytona()\nd.create()\n"
PINNED = load_mapping().v0_version or ""
_MAJOR, _MINOR, _PATCH = (int(part) for part in PINNED.split("."))
NEWER_PATCH = f"{_MAJOR}.{_MINOR}.{_PATCH + 1}"
SCHEMA = json.loads(
    (Path(__file__).parents[2] / "mapping/report.schema.json").read_text()
)


def _run(
    root: Path, capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[int, dict[str, object]]:
    code = main([str(root), "--json", "--yes", "--force", *flags])
    report = json.loads(capsys.readouterr().out)
    jsonschema.validate(report, SCHEMA)
    return code, report


def _project(root: Path, files: dict[str, str]) -> Path:
    for name, text in {"app.py": SOURCE, **files}.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


@pytest.mark.parametrize(
    ("tool_version", "specifier"),
    [
        ("2.0.0a7", "==2.0.0a7"),
        ("2.0.0rc1", "==2.0.0rc1"),
        ("2.3.1", ">=2.3.1,<3"),
        ("3.0.0", ">=3.0.0,<4"),
        ("0.0.0", ">=2,<3"),
        ("0.1.0", ">=2,<3"),
    ],
)
def test_targets_the_sdk_release_the_tool_was_published_with(
    tool_version: str, specifier: str
) -> None:
    # Given / When / Then: a 0.x build keeps the mapping's development specifier.
    assert target_specifier(tool_version, ">=2,<3") == specifier


def test_writes_the_exact_pre_release_the_tool_was_published_with(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 2.0.0a7 tool and a 0.x requirements file.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a7")
    )
    _project(tmp_path, {"requirements.txt": f"daytona=={PINNED}\n"})
    # When
    code, report = _run(tmp_path, capsys)
    # Then: the alpha is pinned, which a >=2,<3 specifier could never install.
    assert code == 0
    assert (tmp_path / "requirements.txt").read_text() == "daytona[v0-sdk]==2.0.0a7\n"
    assert report["nextSteps"] == ['pip install -U "daytona[v0-sdk]==2.0.0a7"']


def test_accepts_a_parent_that_declares_the_exact_target_pre_release(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the parent project was migrated by the same tool release.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a7")
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]==2.0.0a7\n")
    _project(tmp_path / "app", {})
    # When
    _, report = _run(tmp_path / "app", capsys)
    # Then
    items = report["items"]
    assert isinstance(items, list)
    assert [item for item in items if item["kind"] == "manual-dependency"] == []


def test_stays_quiet_on_the_pinned_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    _project(tmp_path, {"requirements.txt": f"daytona=={PINNED}\n"})
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert "warnings" not in report


def test_warns_from_the_lockfile_without_failing_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: pyproject admits the pin, uv.lock installs 0.100.0.
    lock = 'version = 1\n\n[[package]]\nname = "daytona"\nversion = "0.100.0"\n'
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona>=0.100"]\n',
            "uv.lock": lock,
        },
    )
    # When
    code, report = _run(tmp_path, capsys, "--strict")
    # Then: the warning names the lockfile; --strict ignores warnings.
    assert report["warnings"] == [
        {"file": "uv.lock", "message": older_message("0.100.0", PINNED)}
    ]
    assert code == 0


def test_falls_back_to_the_requirement_without_a_lockfile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    _project(tmp_path, {"requirements.txt": "daytona-sdk~=0.150.2\n"})
    _, report = _run(tmp_path, capsys)
    # Then
    assert report["warnings"] == [
        {"file": "requirements.txt", "message": older_message("0.150.2", PINNED)}
    ]


def test_warns_on_a_newer_patch_from_poetry_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    lock = f'[[package]]\nname = "daytona"\nversion = "{NEWER_PATCH}"\n'
    pyproject = f'[tool.poetry]\nname = "app"\n\n[tool.poetry.dependencies]\ndaytona = "^{PINNED}"\n'
    _project(tmp_path, {"pyproject.toml": pyproject, "poetry.lock": lock})
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert report["warnings"] == [
        {"file": "poetry.lock", "message": newer_patch_message(NEWER_PATCH, PINNED)}
    ]


def test_refuses_a_newer_minor_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: Pipfile.lock installs a 0.x minor the bundled .v0 predates.
    lock = json.dumps({"default": {"daytona": {"version": "==0.999.0"}}})
    _project(
        tmp_path,
        {"Pipfile": '[packages]\ndaytona = "*"\n', "Pipfile.lock": lock},
    )
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 2
    assert report["refused"] == {
        "reason": "newer-v0",
        "message": newer_refusal("0.999.0", "Pipfile.lock", PINNED),
    }
    assert (tmp_path / "app.py").read_text() == SOURCE
    assert (tmp_path / "Pipfile").read_text() == '[packages]\ndaytona = "*"\n'


def test_migrates_a_newer_minor_with_allow_newer_v0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    lock = '[[package]]\nname = "daytona-sdk"\nversion = "0.999.0"\n'
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona-sdk"]\n',
            "pdm.lock": lock,
        },
    )
    # When
    code, report = _run(tmp_path, capsys, "--allow-newer-v0")
    # Then
    assert code == 0
    assert report["warnings"] == [
        {"file": "pdm.lock", "message": allowed_newer_message("0.999.0", PINNED)}
    ]
    assert "d.v0.create()" in (tmp_path / "app.py").read_text()


def test_reads_the_workspace_lockfile_above_the_scanned_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    (tmp_path / ".git").mkdir()
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "daytona"\nversion = "0.100.0"\n'
    )
    _project(
        tmp_path / "packages" / "app",
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona"]\n'
        },
    )
    # When
    _, report = _run(tmp_path / "packages" / "app", capsys)
    # Then
    assert report["warnings"] == [
        {"file": "../../uv.lock", "message": older_message("0.100.0", PINNED)}
    ]


def test_warns_when_no_version_can_be_determined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    _project(tmp_path, {"requirements.txt": "daytona\n"})
    _, report = _run(tmp_path, capsys)
    # Then
    assert report["warnings"] == [
        {"file": "requirements.txt", "message": unknown_message(PINNED)}
    ]


def test_prints_warnings_in_the_text_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    _project(tmp_path, {"requirements.txt": "daytona==0.100.0\n"})
    # When
    main([str(tmp_path), "--yes", "--force"])
    # Then
    assert (
        f"requirements.txt: warning: {older_message('0.100.0', PINNED)}"
        in capsys.readouterr().out.splitlines()
    )


def test_ignores_the_version_outside_upgrade_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    _project(tmp_path, {"requirements.txt": "daytona==0.999.0\n"})
    code, report = _run(tmp_path, capsys, "--suggest-v2")
    # Then
    assert code == 0
    assert "refused" not in report
    assert "warnings" not in report


def test_checks_a_hash_locked_requirement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: pip-compile output, which the tool leaves for manual migration.
    _project(
        tmp_path,
        {
            "requirements.txt": "daytona==0.999.0 \\\n    --hash=sha256:"
            + "0" * 64
            + "\n"
        },
    )
    # When
    code, report = _run(tmp_path, capsys)
    # Then: the version is still read, so the newer minor refuses.
    assert code == 2
    assert report["refused"] == {
        "reason": "newer-v0",
        "message": newer_refusal("0.999.0", "requirements.txt", PINNED),
    }


def test_falls_back_to_the_requirement_when_the_lockfile_lacks_the_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a uv.lock for tooling only, beside a requirements file.
    _project(
        tmp_path,
        {
            "uv.lock": '[[package]]\nname = "ruff"\nversion = "0.6.0"\n',
            "requirements.txt": "daytona==0.100.0\n",
        },
    )
    # When
    _, report = _run(tmp_path, capsys)
    # Then
    assert report["warnings"] == [
        {"file": "requirements.txt", "message": older_message("0.100.0", PINNED)}
    ]


def test_reads_only_the_declared_package_from_the_lockfile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the lockfile also lists the old daytona-sdk name at a newer minor.
    lock = (
        f'[[package]]\nname = "daytona"\nversion = "{PINNED}"\n\n'
        '[[package]]\nname = "daytona-sdk"\nversion = "0.999.0"\n'
    )
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona"]\n',
            "uv.lock": lock,
        },
    )
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert "warnings" not in report


def test_reads_the_ancestor_manifest_on_a_partial_root_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the repository root declares a newer minor; only src/ is scanned.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("daytona==0.999.0\n")
    _project(tmp_path / "src", {})
    # When
    code, report = _run(tmp_path / "src", capsys)
    # Then
    assert code == 2
    assert report["refused"] == {
        "reason": "newer-v0",
        "message": newer_refusal("0.999.0", "../requirements.txt", PINNED),
    }


def test_warns_unknown_when_nothing_declares_daytona(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: scripts run against a globally installed SDK, with no manifest.
    _project(tmp_path, {})
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert report["warnings"] == [{"file": ".", "message": unknown_message(PINNED)}]


def test_ignores_versions_from_a_constraints_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: constraints limit versions but install nothing.
    _project(
        tmp_path,
        {
            "requirements.txt": f"-c constraints.txt\ndaytona=={PINNED}\n",
            "constraints.txt": "daytona==0.999.0\n",
        },
    )
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert "warnings" not in report


def test_a_marker_gated_line_does_not_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the newer minor is behind a marker that may never be true.
    requirements = (
        f"daytona=={PINNED}; python_version>='3.8'\n"
        "daytona==0.999.0; python_version<'3.0'\n"
    )
    _project(tmp_path, {"requirements.txt": requirements})
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert "refused" not in report


@pytest.mark.parametrize(
    ("specifier", "warning"),
    [
        (f">{PINNED}", newer_patch_message(NEWER_PATCH, PINNED)),
        ("^0.100 || ^0.999", older_message("0.100.0", PINNED)),
    ],
)
def test_reads_lower_bounds_like_the_typescript_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], specifier: str, warning: str
) -> None:
    # Given: an exclusive bound and Poetry alternatives.
    pyproject = f'[tool.poetry]\nname = "app"\n\n[tool.poetry.dependencies]\ndaytona = "{specifier}"\n'
    _project(tmp_path, {"pyproject.toml": pyproject})
    # When
    _, report = _run(tmp_path, capsys)
    # Then
    assert report["warnings"] == [{"file": "pyproject.toml", "message": warning}]
