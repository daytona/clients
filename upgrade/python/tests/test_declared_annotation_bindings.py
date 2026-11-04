# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Declared annotations follow lexical bindings and concrete assignments."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "binding",
    [
        "class Image: pass",
        "from PIL import Image",
        "from daytona import Image",
        "from daytona.v0 import Image",
    ],
)
def test_preserves_composite_when_bare_type_needs_no_rewrite(binding: str) -> None:
    # Given a local, foreign, or import-migrated bare type.
    source = f'{binding}\nfrom typing import List\ndef f(x: "List[Image]"): pass\n'
    # When migrating its quoted annotation.
    result = migrate(source)
    # Then the annotation does not acquire an unnecessary review marker.
    assert result.items == ()
    assert '"List[Image]"' in result.source


@pytest.mark.parametrize(
    "annotation",
    [
        '"List[daytona.Sandbox]"',
        '"daytona.Sandbox | None"',
        '"Annotated[daytona.Sandbox, daytona.Image]"',
        "\"List['daytona.Sandbox']\"",
    ],
)
def test_rewrites_composite_when_dotted_type_resolves_to_sdk(annotation: str) -> None:
    # Given a real SDK type inside a composite, possibly with value metadata.
    source = f"import daytona\nfrom typing import List, Annotated\ndef f(x: {annotation}): pass\n"
    # When migrating the string expression.
    result = migrate(source)
    # Then only the type reference moves, preserving metadata and quoting.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")
    assert result.items == ()


@pytest.mark.parametrize(
    "binding",
    ["import other as daytona", "class daytona: pass", "import daytona.v0 as daytona"],
)
def test_preserves_composite_when_dotted_owner_is_not_sdk_root(binding: str) -> None:
    # Given a non-root binding with the SDK's spelling.
    source = f'{binding}\ndef f(x: "list[daytona.Image]"): pass\n'
    # When migrating the annotation.
    result = migrate(source)
    # Then spelling alone is not evidence of a moved type.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "value", ['registry["d"]', "registry.get_client()", "Daytona()"]
)
def test_routes_assignment_when_client_was_declared_earlier(value: str) -> None:
    # Given a separate declaration before a runtime assignment.
    source = f"from daytona import Daytona\ny: Daytona\ny = {value}\ny.create()\n"
    # When migrating the later use.
    result = migrate(source)
    # Then explicit type evidence survives the assignment seam.
    assert "y.v0.create()" in result.source


@pytest.mark.parametrize("value", ["None", "123", "Other()"])
def test_preserves_assignment_when_declared_client_has_concrete_conflict(
    value: str,
) -> None:
    # Given a known conflicting runtime value after an SDK declaration.
    source = f"from daytona import Daytona\nclass Other: pass\ny: Daytona\ny = {value}\ny.create()\n"
    # When migrating with both pieces of evidence.
    result = migrate(source)
    # Then the annotation does not override concrete runtime proof.
    assert "y.v0.create()" not in result.source


def test_ignores_declaration_when_assignment_is_in_another_function() -> None:
    # Given identically spelled bindings in distinct lexical scopes.
    source = "from daytona import Daytona\ndef f():\n    y: Daytona\ndef g():\n    y = unknown()\n    y.create()\n"
    # When analyzing both bodies.
    result = migrate(source)
    # Then a sibling declaration cannot supply proof.
    assert "y.v0.create()" not in result.source


@pytest.mark.parametrize(
    "binding",
    ["from mycorp import Daytona", "from daytona import Daytona\nimport plugin"],
)
def test_reports_assignment_when_declared_client_evidence_is_uncertain(
    binding: str,
) -> None:
    # Given an unproven annotation or a conflicting imported-module value.
    value = "plugin" if "import plugin" in binding else "unknown()"
    source = f"{binding}\ny: Daytona\ny = {value}\ny.create()\n"
    # When applying the separate declaration to the runtime assignment.
    result = migrate(source)
    # Then uncertainty requires review, never an unconditional rewrite.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "y.v0.create()" not in result.source


def test_preserves_unpacking_when_declared_client_receives_module() -> None:
    # Given a concrete module assigned through tuple unpacking.
    source = "from daytona import Daytona\nimport plugin\ny: Daytona\n(y,) = (plugin,)\ny.create()\n"
    # When analyzing this compound assignment.
    result = migrate(source)
    # Then simple-assignment annotation proof cannot override the compound value.
    assert "y.v0.create()" not in result.source


@pytest.mark.parametrize("used", ["Daytona", "Image", "Daytona, Image", ""])
def test_keeps_noqa_when_split_names_have_different_usage(used: str) -> None:
    # Given an import whose unused-name suppression is valid before migration.
    source = "from daytona import Daytona, Image  # noqa: F401\n"
    if used:
        source += f"used = ({used})\n"
    # When splitting the import.
    result = migrate(source)
    # Then neither import loses its suppression; opt-in RUF100 is a manual cleanup.
    ruff = [sys.executable, "-m", "ruff"]
    if importlib.util.find_spec("ruff") is None:
        executable = shutil.which("ruff")
        if executable is None:
            pytest.skip("ruff is unavailable as a module or executable")
        ruff = [executable]
    lint = subprocess.run(
        [
            *ruff,
            "check",
            "--isolated",
            "--select",
            "F401",
            "--stdin-filename",
            "app.py",
            "-",
        ],
        input=result.source,
        text=True,
        capture_output=True,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout


@pytest.mark.parametrize("comment", ["# noqa", "# noqa: E501", "# shared types"])
def test_retains_original_comment_on_both_split_lines(comment: str) -> None:
    # Given a trailing comment whose meaning belongs to the original import.
    source = f"from daytona import Daytona, Image  {comment}\n"
    # When splitting the import.
    result = migrate(source)
    # Then the split copies the comment verbatim without guessing its scope.
    assert all(line.endswith(comment) for line in result.source.splitlines())
    assert result.source.count(comment) == 2


def test_strict_succeeds_when_project_contains_local_and_imported_type_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given real SDK code alongside a Daytona-free models file.
    (tmp_path / "requirements.txt").write_text("daytona<1\n")
    models = tmp_path / "models.py"
    source = 'class Image: pass\ndef f(x: "list[Image]"): pass\n'
    models.write_text(source)
    (tmp_path / "app.py").write_text(
        'from daytona import Sandbox\ndef f(x: "list[Sandbox]"): pass\n'
    )
    # When driving strict migration through the CLI.
    status = main([str(tmp_path), "--force", "--strict", "--json"])
    # Then the actual user-facing strict gate stays green without editing models.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert ast.dump(ast.parse(models.read_text())) == ast.dump(ast.parse(source))
