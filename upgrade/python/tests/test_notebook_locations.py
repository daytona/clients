# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook diagnostics address original code cells, not synthetic markers."""

from __future__ import annotations

import json

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.notebooks import read_notebook, transform_notebook


def test_location_when_diagnostic_is_in_later_code_cell() -> None:
    # Given markdown cells interleaved with code and an unproven receiver.
    source = json.dumps(
        {
            "cells": [
                {"cell_type": "markdown", "source": "intro"},
                {"cell_type": "code", "source": "import daytona\n"},
                {"cell_type": "markdown", "source": "details"},
                {"cell_type": "code", "source": "\ndaytona_client.create()\n"},
            ]
        }
    )
    notebook = read_notebook(source)
    mapping = load_mapping()
    context = SourceContext(
        "demo.ipynb", "demo", build_index({"demo": notebook.combined}, mapping)
    )
    # When the notebook adapter transforms and reports the manual call.
    result = transform_notebook(notebook, TransformOptions(mapping), context)
    # Then the location uses the original one-based cell index and cell-local row.
    assert [(item["file"], item["line"], item["column"]) for item in result.items] == [
        ("demo.ipynb#cell=4", 2, 1)
    ]
