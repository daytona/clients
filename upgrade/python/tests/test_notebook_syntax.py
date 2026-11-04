# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""IPython syntax is opaque while real errors keep cell-local coordinates."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from test_notebook_lexical import document, transform

from daytona_upgrade.cli import main


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize(
    "magic",
    [
        "files = !ls",
        "t = %timeit -o sum([1])",
        "files: list[str] = !ls",
        "t: Timing = %timeit -o sum([1])",
        "Daytona?",
        "Daytona??",
        "?Daytona",
        "??Daytona",
        "files = !echo '''",
    ],
)
def test_migrates_when_notebook_contains_assignment_or_help_magic(
    ending: str, magic: str
) -> None:
    # Given magic syntax alongside a real Daytona call.
    code = ending.join(["from daytona import Daytona", magic, "Daytona().create()", ""])
    source = document(code, True)
    # When migrating the notebook.
    result = transform(source)
    # Then the magic and JSON bytes are preserved and only the real call changes.
    assert not result.items
    assert result.source == document(
        code.replace("().create()", "().v0.create()"), True
    )


@pytest.mark.parametrize(
    "code",
    [
        'text = """\nfiles = !ls\nDaytona??\n"""\n',
        'text = "Daytona?"\n',
        "value = 5 % 2\n",
        'text = """\n?Daytona\n"""\n',
    ],
)
def test_preserves_python_when_magic_shape_is_data(code: str) -> None:
    # Given valid Python with magic-shaped data or arithmetic.
    source = document(code, False)
    # When migrating without any executable magic.
    result = transform(source)
    # Then valid Python stays byte-identical.
    assert result.source == source
    assert not result.items


def test_reports_cell_location_when_later_cell_has_real_syntax_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a valid first cell and a second cell broken on its second line.
    source = json.dumps(
        {
            "cells": [
                {"cell_type": "code", "source": "from daytona import Daytona\n"},
                {"cell_type": "code", "source": "Daytona().create()\nx =\n"},
            ]
        }
    )
    path = tmp_path / "demo.ipynb"
    path.write_text(source)
    # When the real CLI tries to migrate the notebook.
    status = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then the error addresses the original cell and no partial migration is written.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["file"], item["line"]) for item in report["items"]] == [
        ("demo.ipynb#cell=2", 2)
    ]
    assert path.read_text() == source


@pytest.mark.parametrize("magic", ["client = !ls", "client: Result = %timeit -o 1"])
def test_loses_proof_when_assignment_magic_replaces_client(magic: str) -> None:
    # Given a magic store that overwrites an earlier client binding.
    code = (
        "from daytona import Daytona\nclient = Daytona()\n"
        + magic
        + "\nclient.create()\n"
    )
    # When masking and migrating the notebook.
    result = transform(document(code, False))
    # Then an unconditional opaque replacement retains neither proof nor linkage.
    assert "client.v0.create()" not in result.source
    assert result.source == document(code, False)
    assert result.items == ()


def test_preserves_runtime_remedy_when_notebook_needs_newer_parser(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given valid 3.10 syntax in a later cell and the real parser's 3.9 grammar.
    source = json.dumps(
        {
            "cells": [
                {"cell_type": "code", "source": "from daytona import Daytona\n"},
                {
                    "cell_type": "code",
                    "source": "match value:\n case 1: Daytona().create()\n",
                },
            ]
        }
    )
    path = tmp_path / "demo.ipynb"
    path.write_text(source)
    parser = ast.parse

    def parse_as_python39(source: str) -> ast.Module:
        return parser(source, feature_version=(3, 9))

    # When migrating with a parser too old to analyze the notebook.
    with monkeypatch.context() as patch:
        if sys.version_info >= (3, 10):
            patch.setattr(ast, "parse", parse_as_python39)
        status = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then the cell-local failure retains the interpreter remedy and writes nothing.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["file"], item["kind"]) for item in report["items"]] == [
        ("demo.ipynb#cell=2", "unsupported-file")
    ]
    assert report["items"][0]["message"] == (
        f"Cannot analyze this source while the tool is running on Python {sys.version_info.major}.{sys.version_info.minor}. "
        "Run the tool with Python >=3.10 (the 2.x SDK requires Python 3.10+) "
        "and an interpreter that supports this file's syntax."
    )
    assert path.read_text() == source
