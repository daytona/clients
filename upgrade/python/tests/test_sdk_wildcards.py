# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""SDK wildcard migrations must preserve imports without silently missing calls."""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize("module", load_mapping().modules)
@pytest.mark.parametrize("separator", ["\n", "; "])
def test_splits_when_sdk_root_imports_everything(module: str, separator: str) -> None:
    # Given a generic receiver whose SDK origin comes only from a wildcard.
    source = f"from {module} import *{separator}d = Daytona()\nd.list()\n"
    # When upgrading both the import surface and its calls.
    result = migrate(source)
    # Then both namespaces import, with the call migrated or explicitly reviewed.
    imports = [
        node.module
        for node in ast.walk(ast.parse(result.source))
        if isinstance(node, ast.ImportFrom)
    ]
    assert imports == ["daytona", "daytona.v0"]
    assert "d.v0.list()" in result.source or any(
        item["kind"] == "fixme" for item in result.items
    )
    assert not any(item["kind"] == "private-import" for item in result.items)


@pytest.mark.parametrize("module", load_mapping().modules)
def test_strict_reports_or_migrates_when_manifest_is_upgraded(
    tmp_path: Path, module: str
) -> None:
    # Given an actual project using a wildcard constructor and a moved method.
    source = tmp_path / "app.py"
    source.write_text(f"from {module} import *\nd = Daytona()\nd.create()\n")
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("daytona==0.224.0\n")
    # When the CLI upgrades the dependency and source together.
    code = main([str(tmp_path), "--yes", "--strict"])
    # Then there is no successful manifest-only migration leaving broken calls.
    output = source.read_text()
    assert "daytona[v0-sdk]>=2,<3" in manifest.read_text()
    assert "from daytona.v0 import *" in output
    assert (code == 0 and "d.v0.create()" in output) or (
        code == 1 and "FIXME(daytona-upgrade)" in output
    )


@pytest.mark.parametrize("separator", ["\n", "; "])
def test_idempotent_when_sdk_wildcard_is_already_split(separator: str) -> None:
    # Given the output of a previous wildcard migration.
    source = f"from daytona import *{separator}from daytona.v0 import *\nd = Daytona()\nd.v0.list()\n"
    # When upgrading the project again.
    result = migrate(source)
    # Then the split does not grow an extra import on each run.
    assert result.source == source


def test_reviews_when_an_unknown_star_can_replace_sdk_constructor() -> None:
    # Given a later wildcard which may shadow the known root constructor.
    source = "from daytona import *\nfrom provider import *\nd = Daytona()\nd.list()\n"
    # When upgrading the unknown client.
    result = migrate(source)
    # Then provenance survives but never certifies a rewrite.
    assert "d.v0.list()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("module", load_mapping().modules)
def test_runs_when_wildcards_target_the_real_bundled_sdk(module: str) -> None:
    # Given an optional isolated interpreter with the built SDK and v0-sdk extra.
    interpreter = os.environ.get("DAYTONA_UPGRADE_TEST_SDK_PYTHON")
    if interpreter is None:
        pytest.skip("set DAYTONA_UPGRADE_TEST_SDK_PYTHON to a bundled SDK interpreter")
    source = (
        f"from {module} import *\ndef optional():\n    try:\n"
        "        return Daytona().list()\n    except DaytonaError:\n        return 'caught'\n"
    )
    migrated = migrate(source)
    exports = json.dumps(load_mapping().v0_exports)
    check = (
        migrated.source
        + "\nimport daytona as root\nimport daytona.v0 as v0\n"
        + "assert 'Daytona' in root.__all__\n"
        + "assert 'Daytona' not in v0.__all__\n"
        + "assert Daytona is root.Daytona\n"
        + "assert DaytonaError is v0.DaytonaError\n"
        + f"assert set({exports}) <= globals().keys()\n"
        + "assert optional() == 'caught'\n"
    )
    # When running the migrated application against the actual installed wheel.
    completed = subprocess.run(
        [interpreter, "-I", "-c", check],
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("DAYTONA_")
        },
        timeout=30,
        check=False,
    )
    # Then the root client survives, legacy names exist, and credentials stay caught.
    assert completed.returncode == 0, completed.stderr
