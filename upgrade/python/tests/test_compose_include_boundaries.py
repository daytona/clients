# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Include uncertainty and relative lookup respect their declaring boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_explicit_dotenv_preflight import CLIENT

from daytona_upgrade.cli import main


@pytest.fixture(autouse=True)
def checkout_environment_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "daytona_upgrade.project_discovery.environment_base", lambda base: base
    )
    monkeypatch.setattr("daytona_upgrade.project.environment_base", lambda base: base)


@pytest.mark.parametrize(
    "source",
    [
        "x-common: &common {image: alpine}\ninclude: [clean.conf]\nservices: {app: *common}\n",
        "include: [clean.conf]\nx-common: &common {image: alpine}\nservices: {app: *common}\n",
        "{x-common: &common {image: alpine}, include: [clean.conf], services: {app: *common}}\n",
    ],
)
def test_passes_when_anchors_are_outside_literal_include(
    tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given unrelated service aliases alongside a resolvable literal include.
    (tmp_path / "compose.yml").write_text(source)
    (tmp_path / "clean.conf").write_text("services: {}\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration inspects the include boundary.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then unrelated YAML indirection does not invent a review item.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []


@pytest.mark.parametrize(
    "include",
    [
        "include: *projects\n",
        "include: [*project]\n",
        "include:\n  - path: [clean.conf, *project]\n",
        "include: [{path: clean.conf, env_file: *project}]\n",
    ],
)
def test_reviews_when_alias_is_inside_include(
    tmp_path: Path, include: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given include values that require alias evaluation.
    (tmp_path / "compose.yml").write_text(
        "x-project: &project clean.conf\nx-projects: &projects [clean.conf]\n" + include
    )
    (tmp_path / "clean.conf").write_text("services: {}\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration cannot prove the include value.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the unresolved environment review remains actionable.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.skipif(sys.platform == "win32", reason="Requires POSIX symlinks")
@pytest.mark.parametrize("custom_base", ["lexical", "canonical", "second"])
def test_refuses_when_symlinked_include_has_custom_nested_candidate(
    tmp_path: Path, custom_base: str
) -> None:
    # Given two lexical include locations sharing one real manifest.
    root = tmp_path / "project"
    root.mkdir()
    files = {
        root / "app.py": CLIENT,
        root / "requirements.txt": "daytona==0.224.0\n",
        root / "compose.yml": "include: [../lexical/link.conf, ../second/link.conf]\n",
        tmp_path / "canonical/base.conf": "include: [child.conf]\n",
    }
    for directory in ("lexical", "canonical", "second"):
        files[tmp_path / directory / "child.conf"] = (
            "services: {app: {environment: {DAYTONA_API_URL: https://self.example/api}}}\n"
            if directory == custom_base
            else "services: {}\n"
        )
    for path, source in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for directory in ("lexical", "second"):
        (tmp_path / directory / "link.conf").symlink_to("../canonical/base.conf")
    # When migration scans every lexical and canonical nested candidate.
    status = main([str(root), "--force", "--yes"])
    # Then no clean alternative masks the endpoint or permits partial writes.
    assert status == 2
    assert {path: path.read_text() for path in files} == files
