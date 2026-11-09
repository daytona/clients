# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The lockstep dependency target and the project's 0.x version check."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from time import perf_counter

import pytest
from report_schema import validate_report

from daytona_upgrade import cli
from daytona_upgrade.cli import main
from daytona_upgrade.dependency_versions import at_or_above_target, target_constraint
from daytona_upgrade.manifests import next_step
from daytona_upgrade.models import load_mapping
from daytona_upgrade.sdk_target import companion_command, target_specifier
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


def _run(
    root: Path, capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[int, dict[str, object]]:
    code = main([str(root), "--json", "--yes", "--force", *flags])
    report = json.loads(capsys.readouterr().out)
    validate_report(report)
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
        '[[package]]\nname = "daytona"\nversion = "0.100.0"\n\n'
        '[[package]]\nname = "app"\nversion = "1"\nsource = { editable = "packages/app" }\n'
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


@pytest.mark.parametrize("nested", [True, False])
def test_a_file_root_reads_the_manifest_beside_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], nested: bool
) -> None:
    # Given: app/main.py beside app/requirements.txt, below or at the Git root.
    (tmp_path / ".git").mkdir()
    project = tmp_path / "app" if nested else tmp_path
    _project(project, {"requirements.txt": "daytona==0.999.0\n"})
    # When
    code = main([str(project / "app.py"), "--json", "--yes", "--force"])
    report = json.loads(capsys.readouterr().out)
    # Then
    assert code == 2
    assert report["refused"]["reason"] == "newer-v0"


def test_an_older_tool_keeps_a_newer_pre_release_pin(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 2.0.0a8 tool on a project already on 2.0.0a9.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a8")
    )
    _project(
        tmp_path,
        {
            "requirements.txt": "daytona[v0-sdk]==2.0.0a9\n",
            "pyproject.toml": '[tool.poetry]\nname = "app"\n\n[tool.poetry.dependencies]\ndaytona = "2.0.0a9"\n',
        },
    )
    # When
    code, report = _run(tmp_path, capsys, "--strict")
    # Then: no downgrade, no manual item.
    assert (tmp_path / "requirements.txt").read_text() == "daytona[v0-sdk]==2.0.0a9\n"
    assert '"2.0.0a9"' in (tmp_path / "pyproject.toml").read_text()
    assert report["dependencies"] == [
        {
            "file": "pyproject.toml",
            "from": 'daytona = "2.0.0a9"',
            "to": 'daytona = {version = "2.0.0a9", extras = ["v0-sdk"]}',
        }
    ]
    assert code == 0


def test_a_rerun_of_a_pre_release_tool_is_clean_under_strict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a project this 2.0.0a8 tool already migrated.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a8")
    )
    _project(tmp_path, {"requirements.txt": f"daytona=={PINNED}\n"})
    _run(tmp_path, capsys)
    # When
    code, report = _run(tmp_path, capsys, "--strict")
    # Then
    assert (tmp_path / "requirements.txt").read_text() == "daytona[v0-sdk]==2.0.0a8\n"
    assert report["changedFiles"] == []
    assert code == 0


def test_a_migrated_declaration_ignores_a_stale_lockfile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the manifest is on 2.x; uv.lock still resolves the old 0.x minor.
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona[v0-sdk]>=2,<3"]\n',
            "uv.lock": '[[package]]\nname = "daytona"\nversion = "0.999.0"\n',
        },
    )
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert "refused" not in report
    assert "warnings" not in report


@pytest.mark.parametrize("member", [False, True])
def test_a_root_lockfile_resolves_only_its_own_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], member: bool
) -> None:
    # Given: the root uv.lock resolves a newer minor; tools/x is on the pin.
    (tmp_path / ".git").mkdir()
    source = (
        '{ editable = "tools/x" }'
        if member
        else '{ registry = "https://pypi.org/simple" }'
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "daytona"\nversion = "0.999.0"\n\n'
        f'[[package]]\nname = "x"\nversion = "1"\nsource = {source}\n'
    )
    _project(
        tmp_path / "tools" / "x",
        {
            "pyproject.toml": f'[project]\nname = "x"\nversion = "1"\ndependencies = ["daytona=={PINNED}"]\n'
        },
    )
    # When
    code, report = _run(tmp_path / "tools" / "x", capsys)
    # Then: a workspace member resolves through the lock; another project does not.
    assert code == (2 if member else 0)
    assert ("refused" in report) is member


def test_a_pre_release_tool_replaces_a_pre_lockstep_range(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: >=2,<3 (what the pre-lockstep tool wrote) installs no 2.x alpha.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a8")
    )
    _project(tmp_path, {"requirements.txt": "daytona[v0-sdk]>=2,<3\n"})
    # When
    _run(tmp_path, capsys)
    # Then
    assert (tmp_path / "requirements.txt").read_text() == "daytona[v0-sdk]==2.0.0a8\n"


def _migrate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
    tool: str,
) -> tuple[str, dict[str, object]]:
    monkeypatch.setattr(cli, "load_mapping", partial(load_mapping, tool_version=tool))
    _project(tmp_path, {"requirements.txt": requirement + "\n"})
    _, report = _run(tmp_path, capsys)
    return (tmp_path / "requirements.txt").read_text().strip(), report


@pytest.mark.parametrize(
    ("requirement", "tool", "expected"),
    [
        ("daytona[v0-sdk]==2.0.0a7", "2.0.0a8", "daytona[v0-sdk]==2.0.0a8"),
        ("daytona[v0-sdk]==2.0.0a8", "2.0.0", "daytona[v0-sdk]>=2.0.0,<3"),
        ("daytona[v0-sdk]>=2.3.1", "2.5.0", "daytona[v0-sdk]>=2.5.0,<3"),
        ("daytona[v0-sdk]>=2.5.0", "2.3.1", "daytona[v0-sdk]>=2.5.0"),
        # A pre-release range also admits the stale 2.0.0rc1: pinned, never lowered.
        ("daytona[v0-sdk]>=2.0.0a9", "2.0.0a8", "daytona[v0-sdk]==2.0.0a9"),
        ("daytona[v0-sdk]>=2.0.0a8", "2.0.0a8", "daytona[v0-sdk]==2.0.0a8"),
        ("daytona[v0-sdk]~=2.0.0a8", "2.0.0a8", "daytona[v0-sdk]==2.0.0a8"),
        # An exclusive bound at or above the target is kept, never rewritten to a version it excludes.
        ("daytona[v0-sdk]>2.0.0a8", "2.0.0a8", "daytona[v0-sdk]>2.0.0a8"),
        ("daytona[v0-sdk]>2.0.0a9", "2.0.0a8", "daytona[v0-sdk]>2.0.0a9"),
        # The published 2.0.0rc1 is a stale pre-alpha build without .v0: not ahead of an alpha.
        ("daytona[v0-sdk]==2.0.0rc1", "2.0.0a8", "daytona[v0-sdk]==2.0.0a8"),
        ("daytona[v0-sdk]>=2.0.0rc1,<3", "2.0.0a8", "daytona[v0-sdk]==2.0.0a8"),
        # Any other beta or rc is ahead of an alpha tool: kept, or pinned to its floor.
        ("daytona[v0-sdk]==2.0.0b1", "2.0.0a8", "daytona[v0-sdk]==2.0.0b1"),
        ("daytona[v0-sdk]>=2.0.0rc2", "2.0.0a8", "daytona[v0-sdk]==2.0.0rc2"),
        ("daytona[v0-sdk]>=2.5.0", "2.0.0a3", "daytona[v0-sdk]>=2.5.0"),
        ("daytona[v0-sdk]==2.1.0", "2.0.0a3", "daytona[v0-sdk]==2.1.0"),
        ("daytona[v0-sdk]>=2,<3", "2.0.0a3", "daytona[v0-sdk]==2.0.0a3"),
        # An exact 2.0.0 pin, in any spelling, is a project already on the stable release: kept.
        ("daytona[v0-sdk]==2.0.0", "2.0.0a3", "daytona[v0-sdk]==2.0.0"),
        ("daytona[v0-sdk]===2.0.0", "2.0.0a3", "daytona[v0-sdk]===2.0.0"),
    ],
)
def test_moves_a_lower_release_of_the_target_major_to_the_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
    tool: str,
    expected: str,
) -> None:
    # Given / When
    written, report = _migrate(tmp_path, capsys, monkeypatch, requirement, tool)
    # Then: a lower release moves to the target; a higher one stays.
    assert written == expected
    items = report["items"]
    assert isinstance(items, list)
    assert [item for item in items if item["kind"] == "manual-dependency"] == []


@pytest.mark.parametrize(
    "requirement", ["daytona[v0-sdk]==3.0.0", "daytona[v0-sdk]>=3"]
)
def test_keeps_a_later_major_and_reports_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
) -> None:
    # Given / When
    written, report = _migrate(tmp_path, capsys, monkeypatch, requirement, "2.0.0a8")
    # Then: never downgraded across a major.
    assert written == requirement
    specifier = requirement.removeprefix("daytona[v0-sdk]")
    assert {
        "file": "requirements.txt",
        "line": 1,
        "column": 1,
        "kind": "manual-dependency",
        "message": f"daytona{specifier} is newer than this daytona-upgrade release targets (==2.0.0a8); use the daytona-upgrade release of that SDK version.",
    } in report["items"]  # type: ignore[operator]


def test_a_kept_newer_pin_gets_no_install_step(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given / When: an older tool on a project already past it.
    _, report = _migrate(
        tmp_path, capsys, monkeypatch, "daytona[v0-sdk]==2.0.0a9", "2.0.0a8"
    )
    # Then: no next step that would downgrade it.
    assert report["dependencies"] == []
    assert report["nextSteps"] == []


@pytest.mark.parametrize(
    ("requirement", "reported"),
    [("daytona[v0-sdk]>=2,<3", True), ("daytona[v0-sdk]==2.0.0a8", False)],
)
def test_an_ancestor_certifies_only_at_or_above_the_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
    reported: bool,
) -> None:
    # Given: the repository root declares the SDK; app/ is scanned.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a8")
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text(requirement + "\n")
    _project(tmp_path / "app", {})
    # When
    _, report = _run(tmp_path / "app", capsys)
    # Then
    items = report["items"]
    assert isinstance(items, list)
    manual = [item for item in items if item["kind"] == "manual-dependency"]
    assert bool(manual) is reported
    if reported:
        assert manual[0]["message"] == (
            "no Daytona dependency was updated; verify an unconditional runtime"
            " daytona[v0-sdk]==2.0.0a8 requirement where the project declares it"
        )


def test_treats_an_editable_development_build_as_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: uv.lock resolves an editable checkout of the SDK.
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona"]\n',
            "uv.lock": '[[package]]\nname = "daytona"\nversion = "0.1.dev1+g1234"\n',
        },
    )
    # When
    _, report = _run(tmp_path, capsys)
    # Then
    assert report["warnings"] == [
        {"file": "pyproject.toml", "message": unknown_message(PINNED)}
    ]


def test_keeps_the_version_warning_on_an_exit_1_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an older 0.x version and an unreadable directory.
    _project(tmp_path, {"requirements.txt": "daytona==0.100.0\n", "locked/a.py": ""})
    (tmp_path / "locked").chmod(0o000)
    try:
        # When
        code = main([str(tmp_path), "--json", "--dry"])
        report = json.loads(capsys.readouterr().out)
    finally:
        (tmp_path / "locked").chmod(0o755)
    # Then: both tools report it.
    assert code == 1
    assert report["warnings"] == [
        {"file": "requirements.txt", "message": older_message("0.100.0", PINNED)}
    ]


def test_reads_a_large_uv_lock_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a workspace member's 4 MB uv.lock.
    (tmp_path / ".git").mkdir()
    filler = "".join(
        f'[[package]]\nname = "pkg-{index}"\nversion = "1.0.0"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
        f'sdist = {{ url = "https://files/{index}.tar.gz", hash = "sha256:{"0" * 64}" }}\n\n'
        for index in range(20_000)
    )
    (tmp_path / "uv.lock").write_text(
        filler
        + '[[package]]\nname = "daytona"\nversion = "0.100.0"\n\n'
        + '[[package]]\nname = "app"\nversion = "1"\nsource = { editable = "app" }\n'
    )
    _project(
        tmp_path / "app",
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona"]\n'
        },
    )
    started = perf_counter()
    # When
    _, report = _run(tmp_path / "app", capsys)
    # Then
    assert perf_counter() - started < 20
    assert report["warnings"] == [
        {"file": "../uv.lock", "message": older_message("0.100.0", PINNED)}
    ]


@pytest.mark.parametrize(
    ("tool_version", "command"),
    [
        ("0.0.0", "npx @daytona/upgrade"),
        ("2.0.0a8", "npx @daytona/upgrade@2.0.0-alpha.8"),
        ("2.0.0rc1", "npx @daytona/upgrade@2.0.0-rc.1"),
        ("2.3.1", "npx @daytona/upgrade@2.3.1"),
    ],
)
def test_names_the_npm_tool_of_the_same_release(
    tool_version: str, command: str
) -> None:
    # Given / When / Then: the companion tool of the same release, not the registry's latest.
    assert companion_command(tool_version) == command


def test_refuses_a_partial_root_with_no_daytona_code_as_no_daytona(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the root declares a newer minor; sub/ has no Daytona code.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("daytona==0.999.0\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.py").write_text("x = 1\n")
    # When
    code, report = _run(tmp_path / "sub", capsys)
    # Then
    assert code == 2
    assert report["refused"]["reason"] == "no-daytona"  # type: ignore[index]
    assert "warnings" not in report


_SHARED = json.loads(
    (Path(__file__).parents[2] / "mapping/v0-version-messages.json").read_text()
)["cases"]


@pytest.mark.parametrize("entry", _SHARED, ids=[case["id"] for case in _SHARED])
def test_prints_the_shared_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], entry: dict[str, object]
) -> None:
    # Given: uv.lock resolves the case's version (upgrade/mapping/v0-version-messages.json).
    def fill(text: object) -> str:
        assert isinstance(text, str)
        return (
            text.replace("{pinned}", PINNED)
            .replace("{newerPatch}", NEWER_PATCH)
            .replace("{file}", "uv.lock")
        )

    version = entry["version"]
    assert isinstance(version, dict)
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona"]\n',
            "uv.lock": f'[[package]]\nname = "daytona"\nversion = "{fill(version["py"])}"\n',
        },
    )
    flags = entry.get("flags", [])
    assert isinstance(flags, list)
    # When
    code, report = _run(tmp_path, capsys, *map(str, flags))
    # Then: the same text as the TypeScript tool; each case names exactly one
    # expectation (a misspelled key fails here).
    assert len({"refusal", "warning"} & entry.keys()) == 1
    if "refusal" in entry:
        assert code == 2
        assert report["refused"]["message"] == fill(entry["refusal"])  # type: ignore[index]
    else:
        assert code == 0
        assert "refused" not in report
        file = "pyproject.toml" if entry.get("unknownFile") else "uv.lock"
        assert report["warnings"] == [{"file": file, "message": fill(entry["warning"])}]


@pytest.mark.parametrize("field", ["markers", "python"])
def test_a_marker_gated_table_entry_is_no_version_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str
) -> None:
    # Given: the newer minor sits behind a condition, as in a requirement string.
    condition = "python_version < '3.0'" if field == "markers" else "<3.0"
    pyproject = f'[tool.poetry]\nname = "app"\n\n[tool.poetry.dependencies]\ndaytona = {{version = "0.999.0", {field} = "{condition}"}}\n'
    _project(tmp_path, {"pyproject.toml": pyproject})
    # When
    code, report = _run(tmp_path, capsys)
    # Then
    assert code == 0
    assert report["warnings"] == [
        {"file": "pyproject.toml", "message": unknown_message(PINNED)}
    ]


def test_prints_a_post_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When
    _project(tmp_path, {"requirements.txt": f"daytona=={PINNED}.post1\n"})
    _, report = _run(tmp_path, capsys)
    # Then: the message names the post-release it compares.
    assert report["warnings"] == [
        {
            "file": "requirements.txt",
            "message": newer_patch_message(f"{PINNED}.post1", PINNED),
        }
    ]


def test_the_install_step_names_the_requirement_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given / When: an older tool pins a newer pre-release range to its own floor.
    written, report = _migrate(
        tmp_path, capsys, monkeypatch, "daytona>=2.0.0a9", "2.0.0a8"
    )
    # Then: the step installs what was written, not the older target.
    assert written == "daytona[v0-sdk]==2.0.0a9"
    assert report["nextSteps"] == ['pip install -U "daytona[v0-sdk]==2.0.0a9"']


def test_refuses_an_abbreviated_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given / When: --allow-newer is not --allow-newer-v0 (the npm tool refuses it too).
    _project(tmp_path, {"requirements.txt": "daytona==0.100.0\n"})
    code = main([str(tmp_path), "--json", "--allow-newer"])
    report = json.loads(capsys.readouterr().out)
    # Then
    assert code == 2
    assert report["refused"]["reason"] == "bad-arguments"


def test_an_exclusive_range_that_excludes_the_target_moves_to_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given / When: >2.0.0,<2.0.1 admits no 2.0.1.
    written, _ = _migrate(
        tmp_path, capsys, monkeypatch, "daytona[v0-sdk]>2.0.0,<2.0.1", "2.0.1"
    )
    # Then
    assert written == "daytona[v0-sdk]>=2.0.1,<3"


@pytest.mark.parametrize(
    ("requirement", "step"),
    [
        ("daytona[v0-sdk]==2.0.0a8", 'pip install -U "daytona[v0-sdk]==2.0.0a8"'),
        (
            'daytona[v0-sdk]==2.0.0a8 ; python_version >= "3.10"',
            "Install the updated requirements.",
        ),
        ("daytona[v0-sdk]==2.0.0a8  # keep", "Install the updated requirements."),
    ],
)
def test_the_install_step_is_a_plain_requirement_or_generic(
    tmp_path: Path, requirement: str, step: str
) -> None:
    # Given / When / Then: no marker or comment inside a quoted shell argument.
    assert next_step(tmp_path, requirement) == step


def test_a_lockfile_with_several_versions_of_the_sdk_is_no_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: uv.lock forks daytona by marker; the requirement names 0.100.0.
    lock = (
        '[[package]]\nname = "daytona"\nversion = "0.100.0"\n\n'
        '[[package]]\nname = "daytona"\nversion = "0.999.0"\n'
    )
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona==0.100.0"]\n',
            "uv.lock": lock,
        },
    )
    # When
    code, report = _run(tmp_path, capsys)
    # Then: the declaration stands in; no refusal from the other fork.
    assert code == 0
    assert report["warnings"] == [
        {"file": "pyproject.toml", "message": older_message("0.100.0", PINNED)}
    ]


def test_reports_an_old_package_name_on_a_later_major(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given / When
    written, report = _migrate(
        tmp_path, capsys, monkeypatch, "daytona-sdk>=3", "2.0.0a8"
    )
    # Then: never renamed and downgraded across a major.
    assert written == "daytona-sdk>=3"
    items = report["items"]
    assert isinstance(items, list)
    assert any(
        item["message"].startswith("daytona-sdk>=3 is newer than") for item in items
    )


def test_a_hash_locked_record_already_at_the_target_needs_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: pip-compile output already on a newer alpha, with the extra.
    record = "daytona[v0-sdk]==2.0.0a9 \\\n    --hash=sha256:" + "0" * 64
    _, report = _migrate(tmp_path, capsys, monkeypatch, record, "2.0.0a8")
    # Then
    items = report["items"]
    assert isinstance(items, list)
    assert [item for item in items if item["kind"] == "manual-dependency"] == []


@pytest.mark.parametrize(
    ("ancestor", "message"),
    [
        (
            "daytona[v0-sdk]>=3",
            "daytona>=3 is newer than this daytona-upgrade release targets (==2.0.0a8); use the daytona-upgrade release of that SDK version.",
        ),
        (
            "daytona[v0-sdk]>=2.0.0a9",
            "no Daytona dependency was updated; verify an unconditional runtime daytona[v0-sdk]==2.0.0a9 requirement where the project declares it",
        ),
    ],
)
def test_ancestor_guidance_never_asks_for_a_downgrade(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    ancestor: str,
    message: str,
) -> None:
    # Given: the repository root declares the SDK above the target; app/ is scanned.
    monkeypatch.setattr(
        cli, "load_mapping", partial(load_mapping, tool_version="2.0.0a8")
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text(ancestor + "\n")
    _project(tmp_path / "app", {})
    # When
    _, report = _run(tmp_path / "app", capsys)
    # Then: the same decision as a rewrite in the project root.
    items = report["items"]
    assert isinstance(items, list)
    assert [
        item["message"] for item in items if item["kind"] == "manual-dependency"
    ] == [message]


def test_the_1x_shim_release_counts_as_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: uv.lock resolves daytona 1.0.1 (a shim that installs daytona-sdk).
    _project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona>=0.100"]\n',
            "uv.lock": '[[package]]\nname = "daytona"\nversion = "1.0.1"\n',
        },
    )
    # When
    _, report = _run(tmp_path, capsys)
    # Then
    assert report["warnings"] == [
        {"file": "pyproject.toml", "message": unknown_message(PINNED)}
    ]


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        # A floor the range excludes: the range is kept, never lowered to the target.
        ("daytona[v0-sdk]>=2.0.0a9,!=2.0.0a9", "daytona[v0-sdk]>=2.0.0a9,!=2.0.0a9"),
    ],
)
def test_never_falls_back_below_a_newer_range(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    requirement: str,
    expected: str,
) -> None:
    # Given / When
    written, _ = _migrate(tmp_path, capsys, monkeypatch, requirement, "2.0.0a8")
    # Then
    assert written == expected


def test_a_poetry_union_is_at_the_target_only_when_every_alternative_is() -> None:
    # Given / When / Then: one alternative below the target is enough to move it.
    assert not at_or_above_target(">2.0.0,<2.0.1 || >=3", ">=2.0.1,<3")
    assert at_or_above_target(">=2.0.1 || >=3", ">=2.0.1,<3")


def test_a_union_whose_floor_no_alternative_admits_is_kept() -> None:
    # Given / When / Then: >=2.0.0a9,!=2.0.0a9 || >=3 admits no 2.0.0a9.
    constraint = ">=2.0.0a9,!=2.0.0a9 || >=3"
    assert (
        target_constraint("daytona", constraint, "daytona", "==2.0.0a8") == constraint
    )


def test_colliding_poetry_declarations_still_give_the_installed_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a half-done rename declares both names, and poetry.lock resolves a newer minor.
    pyproject = (
        '[tool.poetry]\nname = "app"\n\n[tool.poetry.dependencies]\n'
        'daytona = "0.999.0"\ndaytona-sdk = "0.100.0"\n'
    )
    lock = (
        '[[package]]\nname = "daytona"\nversion = "0.999.0"\n\n'
        '[[package]]\nname = "daytona-sdk"\nversion = "0.100.0"\n'
    )
    _project(tmp_path, {"pyproject.toml": pyproject, "poetry.lock": lock})
    # When
    code, report = _run(tmp_path, capsys)
    # Then: both declarations count (the older warns, the newer refuses), nothing written.
    assert code == 2
    assert report["warnings"] == [
        {"file": "poetry.lock", "message": older_message("0.100.0", PINNED)}
    ]
    assert report["refused"]["message"] == newer_refusal(  # type: ignore[index]
        "0.999.0", "poetry.lock", PINNED
    )
    assert (tmp_path / "app.py").read_text() == SOURCE


def test_a_symlinked_project_is_checked_where_it_lives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a uv workspace member reached through a symlink outside the repository.
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "daytona"\nversion = "0.999.0"\n\n'
        '[[package]]\nname = "app"\nversion = "1"\nsource = { editable = "packages/app" }\n'
    )
    _project(
        repo / "packages" / "app",
        {
            "pyproject.toml": '[project]\nname = "app"\nversion = "1"\ndependencies = ["daytona"]\n'
        },
    )
    (tmp_path / "link").symlink_to(repo / "packages" / "app")
    # When
    code, report = _run(tmp_path / "link", capsys)
    # Then: the real lockfile's resolution counts.
    assert code == 2
    assert report["refused"]["reason"] == "newer-v0"  # type: ignore[index]


def test_a_union_floored_on_an_inclusive_alternative_keeps_its_floor() -> None:
    # Given / When / Then: >=2.0.0a9 || >3.0.0 is pinned to its floor, never lowered.
    assert (
        target_constraint("daytona", ">=2.0.0a9 || >3.0.0", "daytona", "==2.0.0a8")
        == "==2.0.0a9"
    )


def test_hash_locked_guidance_never_names_a_downgrade(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: pip-compile output on a newer alpha range, under an older tool.
    record = "daytona[v0-sdk]>=2.0.0a9 \\\n    --hash=sha256:" + "0" * 64
    _, report = _migrate(tmp_path, capsys, monkeypatch, record, "2.0.0a8")
    # Then: the guidance names the range's own floor.
    items = report["items"]
    assert isinstance(items, list)
    assert [
        item["message"] for item in items if item["kind"] == "manual-dependency"
    ] == [
        "Change this dependency to daytona[v0-sdk]==2.0.0a9 manually and regenerate hashes; the original hash-locked or continued record was preserved."
    ]


def test_a_union_with_an_inclusive_alternative_on_the_floor_keeps_it() -> None:
    # Given / When / Then: >2.0.0a9 || >=2.0.0a9 still names 2.0.0a9.
    assert (
        target_constraint("daytona", ">2.0.0a9 || >=2.0.0a9", "daytona", "==2.0.0a8")
        == "==2.0.0a9"
    )


def test_hash_locked_guidance_keeps_the_records_extras(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: pip-compile output with its own extra.
    record = "daytona-sdk[otel]==0.100.0 \\\n    --hash=sha256:" + "0" * 64
    _, report = _migrate(tmp_path, capsys, monkeypatch, record, "2.0.0a8")
    # Then
    items = report["items"]
    assert isinstance(items, list)
    assert [
        item["message"] for item in items if item["kind"] == "manual-dependency"
    ] == [
        "Change this dependency to daytona[otel,v0-sdk]==2.0.0a8 manually and regenerate hashes; the original hash-locked or continued record was preserved."
    ]


def test_an_unreadable_own_declaration_is_unknown_not_an_ancestors_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the root declares a newer minor; app/ installs the SDK from Git.
    (tmp_path / ".git").mkdir()
    (tmp_path / "requirements.txt").write_text("daytona==0.999.0\n")
    _project(
        tmp_path / "app",
        {"requirements.txt": "-e git+https://github.com/daytonaio/sdk#egg=daytona\n"},
    )
    # When
    code, report = _run(tmp_path / "app", capsys)
    # Then: like the npm tool, app's own declaration is unknown.
    assert code == 0
    assert report["warnings"] == [
        {"file": "requirements.txt", "message": unknown_message(PINNED)}
    ]
