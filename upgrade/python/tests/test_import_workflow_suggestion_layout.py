# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Output layout contracts exercised through the real migration surface."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_suggestions import suggest, suggestion_mapping

from daytona_upgrade import cli, models
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.notebooks import read_notebook, transform_notebook
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("indent", ["", "    ", "\t"])
@pytest.mark.parametrize("notebook", [False, True])
def test_import_layout_when_parenthesized_names_are_split(
    tmp_path: Path, indent: str, notebook: bool
) -> None:
    # Given a multiline import, optionally inside an indented suite.
    prefix = "def run():\n" if indent else ""
    original = (
        prefix
        + f"{indent}from daytona import (\n{indent}    BarChart,\n{indent}    Daytona,\n{indent}    Image,\n{indent})\n"
    )
    expected = (
        prefix
        + f"{indent}from daytona import (\n{indent}    Daytona,\n{indent})\n{indent}from daytona.v0 import (\n{indent}    BarChart,\n{indent}    Image,\n{indent})\n"
    )
    path = tmp_path / ("app.ipynb" if notebook else "app.py")
    path.write_text(
        json.dumps({"cells": [{"cell_type": "code", "source": original}]})
        if notebook
        else original
    )
    # When the public CLI splits root and v0 names.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then both closing parentheses align with their statement, in both surfaces.
    actual = (
        json.loads(path.read_text())["cells"][0]["source"]
        if notebook
        else path.read_text()
    )
    assert status == 0
    assert actual == expected


@pytest.mark.parametrize(
    "original,expected",
    [
        (
            "from daytona import (Daytona, Image)\n",
            "from daytona import (Daytona)\nfrom daytona.v0 import (Image)\n",
        ),
        (
            "from daytona import Daytona, Image\n",
            "from daytona import Daytona\nfrom daytona.v0 import Image\n",
        ),
        (
            "from daytona import (\n    Daytona as D,  # client\n    Image as I,  # resource\n)\n",
            "from daytona import (\n    Daytona as D,  # client\n)\nfrom daytona.v0 import (\n    Image as I,  # resource\n)\n",
        ),
        (
            "from daytona import (\n    Image,\n    Daytona\n)\n",
            "from daytona import (\n    Daytona,\n)\nfrom daytona.v0 import (\n    Image,\n)\n",
        ),
    ],
)
def test_original_import_style_when_names_are_split(
    tmp_path: Path, original: str, expected: str
) -> None:
    # Given single-line or commented multiline imports with aliases.
    path = tmp_path / "app.py"
    path.write_text(original)
    # When applying the real CLI transformation.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then style, aliases, and comments survive the split.
    assert status == 0
    assert path.read_text() == expected


def test_workflow_header_when_sandbox_has_no_other_calls() -> None:
    # Given a producing call with no other sandbox operations in its scope.
    source = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n"
    # When guidance is generated from a small known mapping.
    result = suggest(source)
    # Then one compact workflow block precedes the suggestion in the same block.
    assert result.source == (
        "from daytona import Daytona\napi = Daytona()\n"
        "# daytona-v2: workflow `box`: move this sandbox to v2 as a unit\n"
        "# daytona-v2:   data: IDs do not carry over.\n"
        "# daytona-v2:   docs: https://example.test/migrate#data\n"
        '# daytona-v2: api.v2.create("python:3.12")\n'
        "# daytona-v2:   differs: Choose an image.\n"
        "# daytona-v2:   docs: https://example.test/migrate#client-create\n"
        "box = api.v0.create()\n"
    )


def test_workflow_header_when_other_calls_have_a_defined_order() -> None:
    # Given two v0 calls on the produced sandbox.
    source = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\nbox.fs.list_files('.')\nbox.legacy()\n"
    # When guidance gathers the workflow.
    result = suggest(source)
    # Then the header preserves call order and stays before the create suggestion.
    lines = result.source.splitlines()
    assert lines[2:5] == [
        "# daytona-v2: workflow `box`: move these v0 calls on it to v2 together: fs.list_files, legacy",
        "# daytona-v2:   data: IDs do not carry over.",
        "# daytona-v2:   docs: https://example.test/migrate#data",
    ]
    assert lines[5] == '# daytona-v2: api.v2.create("python:3.12")'


def test_data_traps_when_short_text_is_present_or_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a mapping with short, fallback, and intentionally redundant traps.
    mapping_path = tmp_path / "mapping.json"
    raw = json.loads(Path(models.__file__).with_name("mapping.json").read_text())
    raw["dataTraps"] = [
        {"id": "ids", "text": "Full ID explanation.", "short": "new IDs"},
        {"id": "volumes", "text": "volumes migrate separately"},
        {
            "id": "same-platform",
            "text": "must not repeat this",
            "short": "also skipped",
        },
    ]
    mapping_path.write_text(json.dumps(raw))
    mapping = load_mapping(mapping_path)
    base = suggestion_mapping()
    monkeypatch.setattr(
        cli, "load_mapping", lambda: replace(base, data_traps=mapping.data_traps)
    )
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\napi = Daytona()\nbox = api.v0.create()\n"
    )
    # When the CLI renders the parsed mapping.
    status = cli.main([str(tmp_path), "--suggest-v2", "--force", "--yes"])
    # Then exactly one data line uses short-or-full text in mapping order.
    data = [
        line
        for line in (tmp_path / "app.py").read_text().splitlines()
        if line.startswith("# daytona-v2:   data:")
    ]
    assert status == 0
    assert data == ["# daytona-v2:   data: new IDs; volumes migrate separately"]


def test_suggestion_per_call_when_same_id_occurs_twice_in_a_statement() -> None:
    # Given duplicate mapped calls in one statement and another in the next statement.
    source = "from daytona.v0 import Sandbox\ndef run(first: Sandbox, second: Sandbox):\n    paths = (first.fs.list_files('a'), second.fs.list_files('b'))\n    second.fs.list_files('c')\n"
    # When guidance is built for every distinct call site.
    result = suggest(source)
    # Then both receivers get guidance, including the subsequent statement's call.
    entries = [item for item in result.items if item["kind"] == "suggestion"]
    assert [(item["line"], item["column"]) for item in entries] == [
        (3, 14),
        (3, 40),
        (4, 5),
    ]
    assert result.source.count("# daytona-v2: first.fs.list(path)") == 1
    assert result.source.count("# daytona-v2: second.fs.list(path)") == 2


def test_distinct_suggestion_ids_when_their_docs_links_match() -> None:
    # Given two different operations whose guide entries intentionally share a link.
    base = suggestion_mapping()
    mapping = replace(
        base,
        suggestions=(
            *base.suggestions[:2],
            replace(base.suggestions[2], anchor="fs-list"),
        ),
    )
    source = "from daytona.v0 import Sandbox\ndef run(box: Sandbox):\n    result = (box.fs.list_files('.'), box.legacy())\n"
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    # When guidance deduplicates by suggestion identity, not by rendered line text.
    result = transform_source(source, TransformOptions(mapping, "suggest-v2"), context)
    # Then neither distinct suggestion loses its docs line.
    assert (
        result.source.count(
            "# daytona-v2:   docs: https://example.test/migrate#fs-list"
        )
        == 2
    )


def test_workflow_block_when_statement_has_distinct_producing_calls() -> None:
    # Given two alternatives binding the same sandbox in one statement.
    base = suggestion_mapping()
    mapping = replace(
        base,
        suggestions=(
            *base.suggestions,
            replace(
                base.suggestions[0],
                id="client.get",
                method="get",
                form="{client}.v2.get(id)",
                anchor="client-get",
            ),
        ),
    )
    source = "from daytona import Daytona\napi = Daytona()\nbox = api.v0.get('one') or api.v0.create()\n"
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    # When both unique suggestions belong to the same statement block.
    result = transform_source(source, TransformOptions(mapping, "suggest-v2"), context)
    # Then one shared workflow header supplies exactly one data summary and link.
    assert result.source.count("# daytona-v2: workflow `box`:") == 1
    assert result.source.count("# daytona-v2:   data:") == 1
    assert (
        result.source.count("# daytona-v2:   docs: https://example.test/migrate#data")
        == 1
    )
    assert len([item for item in result.items if item["kind"] == "suggestion"]) == 2


def test_notebook_unicode_policy_when_only_metadata_previously_had_unicode() -> None:
    # Given an unescaped-Unicode notebook whose original Python source is ASCII.
    base = suggestion_mapping()
    mapping = replace(
        base,
        suggestions=(replace(base.suggestions[0], form='{client}.v2.create("café")'),),
    )
    original = json.dumps(
        {
            "metadata": {"name": "naïve"},
            "cells": [
                {
                    "cell_type": "code",
                    "source": "from daytona import Daytona\napi = Daytona()\napi.v0.create()\n",
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    notebook = read_notebook(original)
    context = SourceContext(
        "app.ipynb", "app", build_index({"app": notebook.combined}, mapping)
    )
    # When guidance adds non-ASCII text to the changed cell source.
    result = transform_notebook(
        notebook, TransformOptions(mapping, "suggest-v2"), context
    )
    # Then the document's established Unicode escaping policy is retained.
    assert "café" in result.source
    assert r"caf\u00e9" not in result.source
