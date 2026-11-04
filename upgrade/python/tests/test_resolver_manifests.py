# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Resolver declarations must not silently retain incompatible Daytona versions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit
from tomlkit.items import Table

from daytona_upgrade.cli import main
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "package", ["daytona", "daytona-sdk", "Daytona_SDK", "DAYTONA.SDK"]
)
@pytest.mark.parametrize("field", ["override-dependencies", "dev-dependencies"])
def test_migrates_requirement_when_uv_resolver_or_legacy_dev_entry(
    package: str, field: str
) -> None:
    # Given a marked legacy requirement and an unrelated resolver entry.
    source = (
        '[project]\ndependencies = ["daytona==0.224.0"]\n'
        f'[tool.uv]\n{field} = [\'{package}==0.224.0 ; os_name == "nt"\', "httpx>=1"] # keep\n'
    )
    extra = "" if field == "constraint-dependencies" else "[v0-sdk]"
    # When inspecting the complete manifest.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the resolver targets 2.x and preserves markers/trivia.
    assert (
        f'{field} = [\'daytona{extra}>=2,<3 ; os_name == "nt"\', "httpx>=1"] # keep'
        in result.source
    )
    assert "0.224.0" not in result.source
    assert result.manual == ()
    assert (
        inspect_manifest(Path("pyproject.toml"), result.source, load_mapping()).changes
        == ()
    )


@pytest.mark.parametrize(
    "constraint", ["daytona<1", "daytona==0.224.0", "daytona_sdk~=0.224"]
)
def test_reports_manual_dependency_when_constraint_requires_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], constraint: str
) -> None:
    # Given a runtime dependency restricted by a separate resolver constraint.
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\ndependencies = ["daytona==0.224.0"]\n'
        f'[tool.uv]\nconstraint-dependencies = ["{constraint}"]\n'
    )
    # When migrating through the strict CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the constraint is preserved and explicitly requires manual review.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(item["kind"] == "manual-dependency" for item in report["items"])
    tool = tomlkit.parse(path.read_text())["tool"]
    assert isinstance(tool, Table)
    uv = tool["uv"]
    assert isinstance(uv, Table)
    assert uv["constraint-dependencies"] == [constraint]


@pytest.mark.parametrize("requirement", ["daytona", "Daytona_SDK[v0-sdk]>=2,<3"])
def test_migrates_constraint_when_unrestricted_or_target_range(
    requirement: str,
) -> None:
    # Given a constraint whose policy does not need to be discarded.
    source = (
        f"[tool.uv]\nconstraint-dependencies = ['{requirement}; os_name == \"nt\"']\n"
    )
    # When inspecting the resolver declaration.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the constraint uses the normalized target without extras or certification.
    assert (
        "constraint-dependencies = ['daytona>=2,<3; os_name == \"nt\"']"
        in result.source
    )
    assert result.manual == ()
    assert not result.v2_found


@pytest.mark.parametrize(
    "package", ["daytona", "daytona-sdk", "daytona_sdk", "DAYTONA-SDK"]
)
def test_migrates_pdm_override_when_registry_version(package: str) -> None:
    # Given an override pin and an unrelated dependency.
    source = f'[tool.pdm.resolution.overrides]\n{package} = "0.224.0" # keep\nhttpx = "1.0"\n'
    # When inspecting the manifest.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the override resolves the normalized 2.x package, preserving trivia.
    table = tomlkit.parse(result.source)["tool"]
    for key in ("pdm", "resolution", "overrides"):
        assert isinstance(table, Table)
        table = table[key]
    assert table == {
        "daytona": ">=2,<3",
        "httpx": "1.0",
    }
    assert 'daytona = ">=2,<3" # keep' in result.source
    assert result.manual == ()
    assert not result.v2_found
    assert (
        inspect_manifest(Path("pyproject.toml"), result.source, load_mapping()).changes
        == ()
    )


@pytest.mark.parametrize(
    "source",
    [
        '[tool.uv]\noverride-dependencies = ["daytona @ https://example.test/sdk.whl"]\n',
        '[tool.uv]\noverride-dependencies = ["daytona not a requirement"]\n',
        '[tool.pdm.resolution.overrides]\ndaytona = {path = "../sdk"}\n',
        '[tool.pdm.resolution.overrides]\ndaytona = "https://example.test/sdk.whl"\n',
        '[tool.pdm.resolution.overrides]\ndaytona = "0.224.0"\ndaytona_sdk = "0.200"\n',
    ],
)
def test_preserves_resolver_when_unsupported_or_colliding(source: str) -> None:
    # Given resolver syntax that cannot safely be migrated automatically.
    # When inspecting it without modifying a dependency policy.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then it remains intact and cannot silently claim success.
    assert result.source == source
    assert result.manual
    assert result.found
    assert not result.v2_found


@pytest.mark.parametrize(
    "resolver",
    [
        '[tool.uv]\noverride-dependencies = ["daytona==0.224.0"]\n',
        '[tool.pdm.resolution.overrides]\ndaytona = "0.224.0"\n',
    ],
)
def test_upgrades_project_when_resolver_pins_legacy_sdk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], resolver: str
) -> None:
    # Given executable legacy calls with both dependency and resolver pins.
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\ndependencies = ["daytona==0.224.0"]\n' + resolver)
    app = tmp_path / "app.py"
    app.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When applying the migration through the strict CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then both the resolver and code migrate, without unresolved manual work.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert "0.224.0" not in manifest.read_text()
    assert app.read_text() == "from daytona import Daytona\nDaytona().v0.create()\n"


@pytest.mark.parametrize(
    "resolver",
    [
        '[tool.uv]\noverride-dependencies = ["daytona==0.224.0"]\n',
        '[tool.uv]\nconstraint-dependencies = ["daytona<1"]\n',
        '[tool.pdm.resolution.overrides]\ndaytona = "0.224.0"\n',
    ],
)
def test_reports_resolver_when_ancestor_cannot_be_edited(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], resolver: str
) -> None:
    # Given a migrated local dependency beneath an incompatible workspace resolver.
    (tmp_path / ".git").mkdir()
    ancestor = tmp_path / "pyproject.toml"
    ancestor.write_text(resolver)
    project = tmp_path / "member"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\ndependencies = ["daytona[v0-sdk]>=2,<3"]\n'
    )
    (project / "app.py").write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating only the member through the strict CLI.
    status = main([str(project), "--force", "--yes", "--strict", "--json"])
    # Then the read-only ancestor cannot silently keep the resolver on 0.x.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(
        item["kind"] == "manual-dependency" and item["file"] == "../pyproject.toml"
        for item in report["items"]
    )
    assert ancestor.read_text() == resolver


@pytest.mark.parametrize("field", ["override-dependencies", "dev-dependencies"])
def test_override_keeps_user_extras_and_adds_v0_sdk(field: str) -> None:
    # Given an override that requests its own extra.
    source = (
        '[project]\ndependencies = ["daytona[otel]==0.224.0"]\n'
        f'[tool.uv]\n{field} = ["daytona[otel]==0.224.0"]\n'
    )
    # When inspecting the manifest.
    result = inspect_manifest(Path("pyproject.toml"), source, load_mapping())
    # Then the override keeps otel (uv installs it instead of the requirement).
    assert f'{field} = ["daytona[otel,v0-sdk]>=2,<3"]' in result.source
    assert result.manual == ()
