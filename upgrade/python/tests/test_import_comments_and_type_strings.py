# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Import comments survive splitting and quoted types use type-only positions."""

from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "comment",
    [
        "# noqa",
        "# NOQA: F401",
        "#noqa",
        "#noqa: F401",
        "# NoQA: F401",
        "# type: ignore[attr-defined]  # noqa: F401",
        "# shared types  # noqa",
    ],
)
def test_preserves_suppression_when_import_is_split(comment: str) -> None:
    # Given unused imports with a trailing line-scoped suppression.
    source = f"from daytona import Daytona, Image  {comment}\n"
    # When migration splits root names from moved names.
    result = migrate(source)
    # Then both import lines retain the original comment and stay lint-clean.
    assert all(line.endswith("  " + comment) for line in result.source.splitlines())
    ruff = [sys.executable, "-m", "ruff"]
    if importlib.util.find_spec("ruff") is None:
        executable = shutil.which("ruff")
        if executable is None:
            pytest.skip("ruff is unavailable as a module or executable")
        ruff = [executable]
    lint = subprocess.run(
        [*ruff, "check", "--isolated", "--select", "F401", "-"],
        input=result.source,
        text=True,
        capture_output=True,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout


@pytest.mark.parametrize(
    "expression",
    [
        'cast("daytona.Sandbox", value)',
        'typing.cast("list[daytona.Sandbox]", value)',
        'C("daytona.Sandbox", value)',
        'TypeVar("T", bound="daytona.Sandbox")',
        'typing.TypeVar("T", "daytona.Sandbox", "daytona.Image")',
        'TV("T", bound="daytona.Sandbox")',
        'NewType("N", "daytona.Sandbox")',
        'typing.NewType("N", "daytona.Sandbox")',
        'NT("N", "daytona.Sandbox")',
        'typing.cast(typ="daytona.Sandbox", val=value)',
        'NewType("T", tp="daytona.Sandbox")',
        'TypeVar("T", default="daytona.Sandbox")',
        'typing.TypeVar("T", default="daytona.Sandbox")',
        'TV("T", default="daytona.Sandbox")',
        'ParamSpec("P", bound="daytona.Sandbox")',
        'ParamSpec("P", default="daytona.Sandbox")',
        'typing.ParamSpec("P", bound="daytona.Sandbox")',
        'PS("P", default="daytona.Sandbox")',
        'te.TypeVar("T", default="daytona.Sandbox")',
        'te.ParamSpec("P", bound="daytona.Sandbox")',
        'te.ParamSpec("P", default="daytona.Sandbox")',
    ],
)
def test_rewrites_type_string_when_typing_call_is_proven(expression: str) -> None:
    # Given direct, qualified and aliased typing constructors.
    source = (
        "import daytona\nimport typing\nimport typing_extensions as te\n"
        "from typing import cast, TypeVar, NewType, ParamSpec\n"
        "from typing import cast as C, TypeVar as TV, NewType as NT, ParamSpec as PS\n"
        f"result = {expression}\n"
    )
    # When migrating the same type syntax accepted in annotations.
    result = migrate(source)
    # Then every type reference moves, not just its unquoted spelling.
    expected = source.replace("daytona.Sandbox", "daytona.v0.Sandbox").replace(
        "daytona.Image", "daytona.v0.Image"
    )
    assert result.source == expected
    assert result.items == ()


@pytest.mark.parametrize(
    "expression",
    [
        'cast(int, "daytona.Sandbox")',
        'TypeVar("daytona.Sandbox")',
        'NewType("daytona.Sandbox", int)',
        "cast(\"Literal['daytona.Sandbox']\", value)",
        "cast(\"Annotated[int, 'daytona.Sandbox']\", value)",
    ],
)
def test_preserves_value_string_when_typing_call_contains_runtime_data(
    expression: str,
) -> None:
    # Given strings in runtime or annotation-metadata positions.
    source = (
        "import daytona\nfrom typing import cast, TypeVar, NewType, Literal, Annotated\n"
        f"result = {expression}\n"
    )
    # When visiting the typing call.
    result = migrate(source)
    # Then only genuine type positions can be rewritten.
    assert result.source == source


@pytest.mark.parametrize("name", ["cast", "TypeVar", "NewType"])
def test_preserves_strings_when_typing_name_is_shadowed(name: str) -> None:
    # Given a project-owned function with the same spelling as a typing helper.
    source = (
        f"import daytona\nfrom typing import {name}\n"
        f"def {name}(*args, **kwargs): pass\n"
        f'result = {name}("daytona.Sandbox", "daytona.Image", bound="daytona.Sandbox")\n'
    )
    # When migrating the call.
    result = migrate(source)
    # Then spelling alone does not make arbitrary string arguments into types.
    assert result.source == source


def test_preserves_crlf_when_rewriting_multiline_annotation() -> None:
    # Given an entirely CRLF source with newlines inside a type string.
    source = (
        'import daytona\ndef f(x: """list[\n    daytona.Image\n]"""): pass\n'.replace(
            "\n", "\r\n"
        )
    )
    # When the annotation changes.
    result = migrate(source)
    # Then its physical newlines stay CRLF without changing its decoded type value.
    assert result.source == source.replace("daytona.Image", "daytona.v0.Image")
    function = ast.parse(result.source).body[1]
    assert isinstance(function, ast.FunctionDef)
    annotation = function.args.args[0].annotation
    assert isinstance(annotation, ast.Constant)
    assert annotation.value == "list[\n    daytona.v0.Image\n]"


def test_writes_migrated_type_strings_when_cli_upgrades_crlf_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given CRLF source containing both a forward annotation and a typing call.
    path = tmp_path / "app.py"
    source = (
        "import daytona\nfrom typing import cast\n"
        'def f(x: """list[\n    daytona.Image\n]"""):\n'
        '    return cast("daytona.Sandbox", x)\n'
    ).replace("\n", "\r\n")
    path.write_bytes(source.encode())
    # When applying a real CLI migration to disk.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then rewritten type references and every physical newline survive the write.
    assert status == 0, capsys.readouterr()
    assert (
        path.read_bytes()
        == source.replace("daytona.Image", "daytona.v0.Image")
        .replace("daytona.Sandbox", "daytona.v0.Sandbox")
        .encode()
    )


@pytest.mark.parametrize("name", ["app.py", "setup.py"])
def test_reports_location_once_when_cli_cannot_parse_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given a broken Python source or manifest.
    (tmp_path / name).write_text("from daytona import Daytona\nunfinished = (\n")
    # When rendering the human-facing item.
    status = main([str(tmp_path), "--force"])
    # Then the prefix alone supplies the source path and line.
    captured = capsys.readouterr()
    assert status == 1
    line = next(
        line for line in captured.out.splitlines() if "unsupported-file:" in line
    )
    assert line.count(name) == 1
    assert " (line 2)" not in line
