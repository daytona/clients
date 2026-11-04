# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook integration uses the real transform and cross-cell flow analysis."""

from __future__ import annotations

import json

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.notebooks import read_notebook, transform_notebook


def notebook_source(cells: list[str]) -> str:
    return json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": cell.splitlines(keepends=True),
                    "outputs": [{"output_type": "stream", "text": "kept"}],
                    "execution_count": 7,
                    "metadata": {"custom": True},
                }
                for cell in cells
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        },
        separators=(",", ":"),
    )


def test_cross_cell_when_client_created_in_previous_cell() -> None:
    # Given
    original = notebook_source(
        ["from daytona import Daytona\nd = Daytona()\n", "d.create()"]
    )
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext(
        "example.ipynb", "example", build_index({"example": notebook.combined}, mapping)
    )
    # When
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then
    output = json.loads(result.source)
    assert output["cells"][1]["source"] == ["d.v0.create()"]
    assert output["cells"][1]["execution_count"] == 7
    assert output["cells"][1]["outputs"][0]["text"] == "kept"
    assert output["metadata"] == json.loads(original)["metadata"]


def test_original_bytes_when_notebook_unchanged() -> None:
    # Given
    original = notebook_source(["print('hello')\n", "%time print('world')\n"])
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext("example.ipynb", "example", build_index({}, mapping))
    # When
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then
    assert result.source == original


@pytest.mark.parametrize(
    "magic",
    [
        "%pip install daytona==0.224.0 numpy\n",
        "!pip install 'daytona-sdk>=0.1' numpy\n",
    ],
)
def test_requirements_when_pip_magic_present(magic: str) -> None:
    # Given
    original = notebook_source(
        [
            magic,
            "from daytona import Daytona\nd = Daytona()\n",
            "%time d.create()\nd.create()\n",
        ]
    )
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext("example.ipynb", "example", build_index({}, mapping))
    # When
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then
    output = json.loads(result.source)
    assert output["cells"][0]["source"] == [
        '%pip install "daytona[v0-sdk]>=2,<3" numpy\n'
        if magic.startswith("%")
        else '!pip install "daytona[v0-sdk]>=2,<3" numpy\n'
    ]
    assert output["cells"][2]["source"] == ["%time d.create()\n", "d.v0.create()\n"]


def test_cell_magic_when_body_is_not_python() -> None:
    # Given
    original = notebook_source(
        ["%%bash\necho hello !\n", "from daytona import Daytona\nDaytona().create()\n"]
    )
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext("example.ipynb", "example", build_index({}, mapping))
    # When
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then
    assert json.loads(result.source)["cells"][0]["source"] == [
        "%%bash\n",
        "echo hello !\n",
    ]


def test_pip_magic_when_suggestion_mode() -> None:
    # Given
    original = notebook_source(["%pip install daytona\n"])
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext("example.ipynb", "example", build_index({}, mapping))
    # When
    result = transform_notebook(
        notebook, TransformOptions(mapping, "suggest-v2"), context
    )
    # Then
    assert result.source == original
    assert result.found


def test_unrelated_requirement_when_name_ends_with_daytona() -> None:
    # Given
    original = notebook_source(["%pip install not-daytona\n"])
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext("example.ipynb", "example", build_index({}, mapping))
    # When
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then
    assert result.source == original
    assert not result.found


def test_magic_when_it_is_only_statement_in_suite() -> None:
    # Given
    original = notebook_source(
        [
            "from daytona import Daytona\nd = Daytona()\nif True:\n    %time d.create()\nd.create()\n"
        ]
    )
    notebook = read_notebook(original)
    mapping = load_mapping()
    context = SourceContext("example.ipynb", "example", build_index({}, mapping))
    # When
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then
    assert not result.items
    assert json.loads(result.source)["cells"][0]["source"][-2:] == [
        "    %time d.create()\n",
        "d.v0.create()\n",
    ]
