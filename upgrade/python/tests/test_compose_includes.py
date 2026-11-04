# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compose includes preserve environment refusal and conservative review."""

from __future__ import annotations

import json
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
    "include",
    [
        "include: [nested/base.conf]\n",
        "include:\n  - nested/base.conf\n",
        "include:\n- path: nested/base.conf\n",
        "include: [{path: [clean.conf, nested/base.conf]}]\n",
        "include:\n  - path:\n      - clean.conf\n      - nested/base.conf\n",
        '{"include": [{"path": "nested/base.conf"}]}\n',
        "include: [\n  clean.conf,\n  nested/base.conf\n]\nservices: {}\n",
    ],
)
@pytest.mark.parametrize("endpoint", ["inline", "service", "recursive"])
def test_refuses_without_writes_when_include_loads_custom_endpoint(
    tmp_path: Path, include: str, endpoint: str
) -> None:
    # Given custom-named included manifests and a recursive include cycle.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        "staging.yml": include,
        "clean.conf": "services: {}\n",
        "nested/base.conf": (
            "services: {app: {environment: {DAYTONA_API_URL: https://self.example/api}}}\n"
            if endpoint == "inline"
            else "services: {app: {env_file: runtime.conf}}\n"
            if endpoint == "service"
            else "include: [deeper.conf]\n"
        ),
        "nested/deeper.conf": "include: [base.conf]\nservices: {app: {env_file: runtime.conf}}\n",
        "nested/runtime.conf": "DAYTONA_API_URL=https://self.example/api\n",
    }
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    # When migration follows includes through the public CLI.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then a custom endpoint refuses atomically, including through cycles.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "entry",
    [
        "include:\n  - path: nested/base.conf\n    env_file: runtime.conf\n",
        "include: [{path: nested/base.conf, env_file: [clean.conf, runtime.conf]}]\n",
        "include:\n  - path: [nested/base.conf]\n    env_file:\n      - clean.conf\n      - runtime.conf\n",
        "include:\n  - env_file: runtime.conf\n    project_directory: runtime\n    path: nested/base.conf\n",
        "include: [{path: nested/base.conf, project_directory: runtime, env_file: runtime.conf}]\n",
    ],
)
@pytest.mark.parametrize(
    "custom_base", [".", "nested", "runtime", "nested/runtime", "other"]
)
def test_refuses_when_include_environment_has_custom_endpoint_at_candidate_base(
    tmp_path: Path, entry: str, custom_base: str
) -> None:
    # Given clean alternatives at all conservative and include-specific bases.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        "compose.yml": entry,
        "nested/base.conf": "services: {}\n",
        "other/compose.yml": "services: {}\n",
    }
    # A runtime directory is applicable only with explicit project_directory.
    if custom_base.endswith("runtime") and "project_directory" not in entry:
        entry = (
            entry.replace("path:", "project_directory: runtime, path:")
            if "[{" in entry
            else entry.replace("  - path:", "  - project_directory: runtime\n    path:")
        )
        files["compose.yml"] = entry
    for directory in [".", "nested", "runtime", "nested/runtime", "other"]:
        files[f"{directory}/runtime.conf"] = (
            "DAYTONA_API_URL=https://self.example/api\n"
            if directory == custom_base
            else "OTHER=value\n"
        )
        files[f"{directory}/clean.conf"] = "OTHER=value\n"
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    # When every existing environment candidate is checked.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then a clean alternative cannot mask the custom endpoint.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "include",
    [
        "include: [${MANIFEST}]\n",
        "include: *projects\n",
        "include: [{path: clean.conf, env_file: *environment}]\n",
        "include: [{path: clean.conf, env_file: ${ENVIRONMENT}}]\n",
        "include: [{path: clean.conf, project_directory: ${DIRECTORY}}]\n",
        "include: [{path: clean.conf, env_file: missing.conf}]\n",
        "include: [missing.conf]\n",
        "include: [clean.conf}\n",
        "include: [clean.conf] trailing\n",
        "include:\n  - path: clean.conf\n    unexpected: value\n",
        "x: &projects {include: [clean.conf]}\n<<: *projects\nservices: {}\n",
        "x: &project clean.conf\ninclude: [clean.conf, *project]\n",
        "include: [{path: []}]\n",
    ],
)
def test_reviews_when_include_inputs_are_unresolved(
    tmp_path: Path, include: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an include outside the provable literal subset.
    (tmp_path / "compose.yml").write_text(include)
    (tmp_path / "clean.conf").write_text("services: {}\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict preflight cannot establish the environment.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the existing review item is retained.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


def test_passes_when_clean_includes_form_a_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a literal include cycle without environment writes.
    (tmp_path / "compose.yml").write_text("include: [child.conf]\n")
    (tmp_path / "child.conf").write_text("include: [compose.yml]\nservices: {}\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration follows each manifest once.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the cycle terminates without inventing a review item.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []


@pytest.mark.parametrize("filename", ["Dockerfile", "base.conf"])
def test_refuses_when_external_include_uses_a_project_directory(
    tmp_path: Path, filename: str
) -> None:
    # Given an external include whose service environment is project-relative.
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "runtime").mkdir()
    files = {
        root / "app.py": CLIENT,
        root / "requirements.txt": "daytona==0.224.0\n",
        root
        / "compose.yml": f"include: [{{path: ../{filename}, project_directory: runtime}}]\n",
        tmp_path / filename: "services: {app: {env_file: settings.conf}}\n",
        tmp_path
        / "runtime/settings.conf": "DAYTONA_API_URL=https://self.example/api\n",
    }
    for path, source in files.items():
        path.write_text(source)
    # When the filename and checkout boundary cannot select a different parser.
    status = main([str(root), "--force", "--yes"])
    # Then Compose service rules still refuse before writes.
    assert status == 2
    assert {path: path.read_text() for path in files} == files


@pytest.mark.parametrize("loader", ["path", "env_file"])
def test_reviews_when_include_input_is_unreadable(
    tmp_path: Path, loader: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a literal include input with invalid UTF-8 bytes.
    entry = (
        "path: unreadable.conf"
        if loader == "path"
        else "path: clean.conf, env_file: unreadable.conf"
    )
    (tmp_path / "compose.yml").write_text(f"include: [{{{entry}}}]\n")
    (tmp_path / "unreadable.conf").write_bytes(b"\xff\xfe")
    (tmp_path / "clean.conf").write_text("services: {}\n")
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict preflight attempts to read the declared input.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then unreadability remains environment review.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "env-api-url" for item in report["items"])


@pytest.mark.parametrize(
    "loader",
    [
        "services: {app: {env_file: runtime.conf}}\n",
        "include: [{path: clean.conf, env_file: runtime.conf}]\n",
    ],
)
def test_refuses_when_another_include_adds_a_candidate_project_base(
    tmp_path: Path, loader: str
) -> None:
    # Given an include directory shared by all Compose loader candidate searches.
    files = {
        "app.py": CLIENT,
        "requirements.txt": "daytona==0.224.0\n",
        "first.yml": loader,
        "second.yml": "include: [nested/base.conf]\n",
        "clean.conf": "services: {}\n",
        "runtime.conf": "OTHER=value\n",
        "nested/base.conf": "services: {}\n",
        "nested/runtime.conf": "DAYTONA_API_URL=https://self.example/api\n",
    }
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    # When discovery expands all manifests before computing candidate bases.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then a clean project-root candidate never masks the included base.
    assert status == 2
    assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "source",
    [
        'services: {app: {command: "include: [missing.conf]"}}\n',
        "services:\n  app:\n    command: |\n      include: [missing.conf]\n",
        "services: {} # include: [missing.conf]\n",
        "services: {app: {environment: {include: missing.conf}}}\n",
        "x-config: {include: [missing.conf]}\nservices: {}\n",
    ],
)
def test_passes_when_include_text_is_not_an_active_loader(
    tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given include-shaped text outside a top-level Compose include.
    (tmp_path / "compose.yml").write_text(source)
    (tmp_path / "app.py").write_text(CLIENT)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When strict migration scans actual loader boundaries.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then inert text does not invent a review item.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
