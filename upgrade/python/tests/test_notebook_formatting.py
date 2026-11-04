# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook rewrites preserve JSON bytes outside changed cell sources."""

from __future__ import annotations

import json

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.notebooks import read_notebook, transform_notebook


def upgrade(source: str) -> str:
    notebook = read_notebook(source)
    mapping = load_mapping()
    context = SourceContext(
        "example.ipynb", "example", build_index({"example": notebook.combined}, mapping)
    )
    return transform_notebook(notebook, TransformOptions(mapping), context).source


@pytest.mark.parametrize("indent", [None, 1, 2, 4, "\t"])
@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
@pytest.mark.parametrize("array_source", [False, True])
@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_json_bytes_when_only_one_cell_changes(
    indent: int | str | None,
    ending: str,
    array_source: bool,
    ensure_ascii: bool,
) -> None:
    # Four independent pytest axes cover indentation, EOF, source shape, and Unicode policy.
    # Given
    code = 'from daytona import Daytona\nd = Daytona()\nd.create(name="café")\n'
    document = {
        "metadata": {"source": "nested source café", "custom": [1, 2]},
        "cells": [
            {"cell_type": "markdown", "source": ["café"], "metadata": {}},
            {
                "outputs": [{"source": "output source", "text": "café"}],
                "cell_type": "code",
                "metadata": {"source": "cell metadata source"},
                "source": code.splitlines(keepends=True) if array_source else code,
                "execution_count": 42,
            },
            {"cell_type": "code", "source": ["print('café')\n"]},
        ],
        "nbformat_minor": 5,
        "nbformat": 4,
    }
    original = json.dumps(document, indent=indent, ensure_ascii=ensure_ascii)
    if ending == "\r\n":
        original = original.replace("\n", "\r\n")
    original += ending
    expected = original.replace("d.create(", "d.v0.create(")
    # When
    actual = upgrade(original)
    # Then
    assert actual.encode("utf-8") == expected.encode("utf-8")


def test_opaque_whitespace_when_nested_source_keys_surround_code() -> None:
    # Given
    original = (
        '{ "source" : "root", "metadata" : { "cells": [{"source":"nested"}], '
        '"odd" : [ 1 ,  2 ], "unicode":"\\u00e9" },\r\n'
        '"cells" : [ { "source" : [ "from daytona import Daytona\\n" ,\r\n'
        '  "Daytona().create()\\n" ], "cell_type" : "code", '
        '"outputs" : [ { "source" : "untouched" } ] }, '
        '{"cell_type":"raw","source":["leave me"]} ], "nbformat":4 }'
    )
    # When
    actual = upgrade(original)
    # Then
    assert actual == original.replace("Daytona().create()", "Daytona().v0.create()")


@pytest.mark.parametrize("array_source", [False, True])
@pytest.mark.parametrize("indent", [None, 1, 2, 4, "\t"])
def test_source_layout_when_import_rewrite_adds_a_line(
    array_source: bool, indent: int | str | None
) -> None:
    # Given
    code = "from daytona import Daytona, Sandbox\n"
    updated = "from daytona import Daytona\nfrom daytona.v0 import Sandbox\n"
    before = code.splitlines(keepends=True) if array_source else code
    after = updated.splitlines(keepends=True) if array_source else updated
    original = json.dumps(
        {"cells": [{"cell_type": "code", "source": before}], "metadata": {}},
        indent=indent,
    )
    expected = json.dumps(
        {"cells": [{"cell_type": "code", "source": after}], "metadata": {}},
        indent=indent,
    )
    # When
    actual = upgrade(original)
    # Then
    assert actual == expected


@pytest.mark.parametrize("separator", [",", ", "])
def test_compact_array_when_rewrite_adds_a_line(separator: str) -> None:
    # Given
    original = (
        '{"cells":[{"cell_type":"code"'
        + separator
        + '"source":["from daytona import Daytona, Sandbox\\n"]}]}'
    )
    expected = original.replace(
        '"from daytona import Daytona, Sandbox\\n"',
        '"from daytona import Daytona\\n"'
        + separator
        + '"from daytona.v0 import Sandbox\\n"',
    )
    # When
    actual = upgrade(original)
    # Then
    assert actual == expected


def test_multiple_spans_when_json_keys_are_escaped() -> None:
    # Given
    original = (
        '{"cells":[{"cell_type":"code", "so\\u0075rce":'
        '"from daytona import Daytona\\nd = Daytona()\\nd.create()"},'
        '{"cell_type":"code","source":["d.create()\\n"]}],'
        '"metadata":{"source":"d.create()"}}\r\n'
    )
    expected = original.replace("d.create()", "d.v0.create()", 2)
    # When
    actual = upgrade(original)
    # Then
    assert actual == expected
