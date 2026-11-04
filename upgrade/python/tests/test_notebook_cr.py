# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook CR physical lines survive masking and restoration."""

from __future__ import annotations

import json

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.notebooks import read_notebook, transform_notebook


@pytest.mark.parametrize("ending", ["\r", "\r\n", "\n"])
def test_terminators_when_magic_and_calls_are_migrated(ending: str) -> None:
    # Given code with a physical pip magic after the first row.
    code = ending.join(
        [
            "x = 1",
            "%pip install daytona==0.224.0",
            "from daytona import Daytona",
            "Daytona().create()",
            "",
        ]
    )
    notebook = read_notebook(
        json.dumps({"cells": [{"cell_type": "code", "source": code}]})
    )
    mapping = load_mapping()
    context = SourceContext("demo.ipynb", "demo", build_index({}, mapping))
    # When restoring the rewritten code cell after synthetic-module analysis.
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then the executable changes preserve every original physical terminator.
    expected = code.replace("daytona==0.224.0", '"daytona[v0-sdk]>=2,<3"').replace(
        "Daytona().create()", "Daytona().v0.create()"
    )
    assert json.loads(result.source)["cells"][0]["source"] == expected
