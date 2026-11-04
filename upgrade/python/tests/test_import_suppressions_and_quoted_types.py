# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regressions for split suppressions, declared targets, and quoted types."""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys

import pytest
from test_flow import migrate


@pytest.mark.parametrize("used", ["Daytona", "Image", "Daytona, Image", ""])
def test_retains_suppression_when_directive_has_trailing_explanation(
    used: str,
) -> None:
    # Given a split-worthy import with an attributed suppression plus explanation.
    source = "from daytona import Daytona, Image  # noqa: F401  # explanation\n"
    if used:
        source += f"used = ({used})\n"
    # When migrating its import layout.
    result = migrate(source)
    # Then unused imports remain suppressed, preserving the original comment.
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
            "--ignore",
            "E501",
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
    assert result.source.count("# explanation") == 2


def test_retains_other_suppression_when_trailing_comment_follows_codes() -> None:
    # Given an intentionally long import line with both unused and length rules.
    alias = "Client" * 20
    source = f"from daytona import Daytona as {alias}, Image  # noqa: F401, E501  # explanation\n"
    # When splitting names across root and resource imports.
    result = migrate(source)
    # Then the first line retains its length rule and both retain unused rules.
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
            "F401,E501",
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
    assert result.source.count("# explanation") == 2


def test_keeps_bare_noqa_on_both_lines_when_it_has_trailing_comment() -> None:
    # Given a blanket directive with explanatory trailing text.
    source = "from daytona import Daytona, Image  # noqa  # explanation\n"
    # When migrating the import.
    result = migrate(source)
    # Then both lines retain the directive and its explanation verbatim.
    assert all(
        line.endswith("# noqa  # explanation") for line in result.source.splitlines()
    )
    assert result.source.count("# noqa") == 2


@pytest.mark.parametrize(
    "assignment",
    [
        "client, other = (unknown(), value)",
        "[client, other] = [unknown(), value]",
        "other, (client, extra) = (value, (unknown(), value))",
        "other, [client, extra] = (value, [unknown(), value])",
    ],
)
def test_retains_declaration_when_name_is_in_literal_unpacking(assignment: str) -> None:
    # Given a separately declared client assigned an unresolved unpacked value.
    source = (
        f"from daytona import Daytona\nclient: Daytona\n{assignment}\nclient.create()\n"
    )
    # When resolving the later receiver.
    result = migrate(source)
    # Then nested targets carry the same explicit proof as a simple assignment.
    assert "client.v0.create()" in result.source
    assert result.items == ()


@pytest.mark.parametrize("quote", ["'", '"'])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "annotation",
    [
        'Annotated["daytona.Sandbox", "it\'s"]',
        "Annotated['daytona.Sandbox', 'say \"hi\"']",
    ],
)
def test_escapes_annotation_when_retained_metadata_contains_quotes(
    quote: str, nested: bool, annotation: str
) -> None:
    # Given metadata containing either delimiter, optionally in another forward ref.
    value = repr(annotation) if nested else annotation
    literal = repr(value) if quote == "'" else json.dumps(value)
    source = (
        f"import daytona\nfrom typing import Annotated\ndef f(x: {literal}): pass\n"
    )
    # When migrating the quoted type reference.
    result = migrate(source)
    # Then both annotation layers parse and metadata keeps its original value.
    tree = ast.parse(result.source)
    function = tree.body[-1]
    assert isinstance(function, ast.FunctionDef)
    outer = function.args.args[0].annotation
    assert isinstance(outer, ast.Constant) and isinstance(outer.value, str)
    rewritten = ast.parse(outer.value, mode="eval").body
    if nested:
        assert isinstance(rewritten, ast.Constant) and isinstance(rewritten.value, str)
        rewritten = ast.parse(rewritten.value, mode="eval").body
    assert ast.dump(rewritten) == ast.dump(
        ast.parse(
            annotation.replace("daytona.Sandbox", "daytona.v0.Sandbox"), mode="eval"
        ).body
    )
    assert result.items == ()


def test_keeps_carriage_return_in_triple_quoted_annotation() -> None:
    # Given a triple-quoted forward reference with an escaped CR between tokens.
    source = (
        "import daytona\nfrom typing import Annotated\n"
        'def f(x: """Annotated[\\rdaytona.Sandbox]"""): pass\n'
    )
    # When migrating it.
    result = migrate(source)
    # Then the rewritten annotation still holds the CR, not a normalized LF.
    function = ast.parse(result.source).body[-1]
    assert isinstance(function, ast.FunctionDef)
    outer = function.args.args[0].annotation
    assert isinstance(outer, ast.Constant)
    assert outer.value == "Annotated[\rdaytona.v0.Sandbox]"
