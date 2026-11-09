# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Project boundaries preserve import ownership and migration safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from report_schema import validate_report

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import Resolver
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("name", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize("layout", ["module", "package", "src"])
def test_refuses_when_project_owns_sdk_module(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, layout: str
) -> None:
    # Given a first-party module that would collide with SDK imports.
    relative = {
        "module": f"{name}.py",
        "package": f"{name}/__init__.py",
        "src": f"src/{name}/__init__.py",
    }[layout]
    local = tmp_path / relative
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("class Daytona:\n def create(self): return 'local'\n")
    app = tmp_path / "app.py"
    source = f"from {name} import Daytona\nDaytona().create()\n"
    app.write_text(source)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona-sdk==0.226.0\n")
    # When the CLI attempts an upgrade, even with the URL override.
    status = main([str(tmp_path), "--force", "--json", "--drop-api-url"])
    # Then it refuses atomically with a schema-valid, actionable reason.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "local-sdk-module"
    assert f"`{name}`" in report["refused"]["message"]
    assert (
        f"({(local if layout == 'module' else local.parent).relative_to(tmp_path).as_posix()})"
        in report["refused"]["message"]
    )
    assert report["changedFiles"] == []
    assert app.read_text() == source
    assert requirements.read_text() == "daytona-sdk==0.226.0\n"
    validate_report(report)


@pytest.mark.parametrize(
    "directory", [".venv", "venv", "site-packages", "build", "dist", "node_modules"]
)
def test_upgrades_when_colliding_package_is_in_skipped_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], directory: str
) -> None:
    # Given an installed/generated package, not project-owned source.
    package = tmp_path / directory / "daytona"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("class Daytona: pass\n")
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When the CLI discovers and upgrades actual project source.
    status = main([str(tmp_path), "--force", "--json"])
    # Then skipped packages neither refuse nor capture the SDK binding.
    assert status == 0
    assert "refused" not in json.loads(capsys.readouterr().out)
    assert "Daytona().v0.create()" in app.read_text()


@pytest.mark.parametrize("module", ["daytona", "daytona_sdk", "daytona.helpers"])
def test_local_import_has_priority_when_indexed(module: str) -> None:
    # Given a local class with a name also exported by the SDK mapping.
    mapping = load_mapping()
    source = f"from {module} import Daytona\nDaytona().create()\n"
    index = build_index({module: "class Daytona: pass\n", "app": source}, mapping)
    # When resolving the imported name through the real project index.
    kind = Resolver(mapping, index).imported(module, "Daytona")
    # Then the local class, not SDK client proof, wins.
    assert kind.startswith("class:")


@pytest.mark.parametrize("module", ["daytona", "daytona_sdk", "daytona.helpers"])
def test_import_stays_local_when_indexed(module: str) -> None:
    # Given first-party imports, including a one-line suite and plain import.
    mapping = load_mapping()
    source = (
        f"from {module} import Daytona\nif True: from {module} import Sandbox\n"
        f"import {module} as local\nlocal.Sandbox\nlocal.Daytona().create()\n"
        f"from importlib import import_module\nimport_module('{module}')\n"
        f"from unittest.mock import patch\npatch('{module}.Daytona.create')\n"
    )
    index = build_index({module: "class Daytona: pass\n", "app": source}, mapping)
    # When the codemod runs without the CLI refusal boundary.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )
    # Then it never relocates project modules under the installed SDK.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "root_relative", [".", "deploy", "deploy/nested", "deploy/nested/values.yaml"]
)
def test_refuses_custom_url_when_scan_root_is_inside_deployment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], root_relative: str
) -> None:
    # Given the same deployment file under any supported scan root.
    deployment = tmp_path / "deploy/nested"
    deployment.mkdir(parents=True)
    (deployment / "values.yaml").write_text(
        "DAYTONA_API_URL: https://daytona.internal.corp/api\n"
    )
    (deployment / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    # When the real CLI scans the root, deployment directory, or file.
    status = main([str(tmp_path / root_relative), "--dry", "--json"])
    # Then the URL refusal cannot be bypassed by narrowing the root.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert report["refused"]["reason"] == "custom-api-url"
    assert any(item["kind"] == "custom-api-url" for item in report["items"])


def test_template_is_ignored_when_not_a_requirements_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a non-manifest template containing a dependency-looking line.
    template = tmp_path / "index.html.in"
    template.write_text("daytona-sdk==0.226.0\n")
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.226.0\n")
    # When a strict upgrade scans the project.
    status = main([str(tmp_path), "--force", "--strict", "--json"])
    # Then the HTML template is not treated as a pip requirement.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["changedFiles"] == ["requirements.txt"]
    assert template.read_text() == "daytona-sdk==0.226.0\n"


@pytest.mark.parametrize(
    "name",
    [
        "requirements.in",
        "requirements-dev.in",
        "constraints.in",
        "constraints-dev.in",
        "index.html.in",
    ],
)
@pytest.mark.parametrize("option", ["-r", "-c"])
def test_requirements_input_is_migrated_when_named_or_referenced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, option: str
) -> None:
    # Given a conventional input or an explicitly included arbitrary template.
    dependency = tmp_path / name
    dependency.write_text("daytona-sdk==0.226.0\n")
    (tmp_path / "requirements.txt").write_text(f"{option} {name}\n")
    # When pip include semantics select the file as a manifest.
    status = main([str(tmp_path), "--force", "--json"])
    # Then the selected manifest migrates, preserving constraint semantics.
    assert status == 0
    assert name in json.loads(capsys.readouterr().out)["changedFiles"]
    assert dependency.read_text() == (
        "daytona>=2,<3\n" if option == "-c" else "daytona[v0-sdk]>=2,<3\n"
    )


def test_fixmes_are_kept_when_suggestions_are_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given generated guidance and an unresolved manual-review comment.
    app = tmp_path / "app.py"
    source = "# FIXME(daytona-upgrade): review this call\nclient.create()\n"
    app.write_text("# daytona-v2: optional guidance\n" + source)
    # When invoking the documented removal-only mode.
    status = main([str(tmp_path), "--force", "--json", "--remove-suggestions"])
    # Then only optional suggestions are removed, not the user's manual work.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["changedFiles"] == ["app.py"]
    assert app.read_text() == source
