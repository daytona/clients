# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook lexical boundaries distinguish executable magics from string data."""

from __future__ import annotations

import json

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import Mode, SourceResult, TransformOptions, load_mapping
from daytona_upgrade.notebooks import read_notebook, transform_notebook


def document(code: str, array: bool) -> str:
    return json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": code.splitlines(keepends=True) if array else code,
                    "outputs": [{"text": "untouched"}],
                    "metadata": {"custom": True},
                    "execution_count": 7,
                }
            ],
            "metadata": {"custom": "kept"},
        },
        indent=2,
    )


def transform(source: str, mode: Mode = "upgrade") -> SourceResult:
    notebook = read_notebook(source)
    mapping = load_mapping()
    context = SourceContext(
        "app.ipynb", "app", build_index({"app": notebook.combined}, mapping)
    )
    return transform_notebook(notebook, TransformOptions(mapping, mode), context)


@pytest.mark.parametrize("array", [False, True])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "literal",
    [
        f"{quote}\n{prefix} daytona==0.224.0\n{quote[-3:]}"
        for quote in ['"""', "r'''", 'f"""', 'b"""']
        for prefix in ["%pip install", "!pip install", "?daytona"]
    ],
)
def test_magic_prefix_is_data_when_inside_multiline_string(
    array: bool, ending: str, literal: str
) -> None:
    # Given pip-like data and sibling IPython prefixes.
    code = "text = " + literal + "\n"
    source = document(code.replace("\n", ending), array)
    # When upgrading a notebook with no executable magic.
    result = transform(source)
    # Then its complete JSON and literal bytes are unchanged.
    assert result.source == source
    assert not result.items
    assert not result.found


def test_modulo_stays_python_when_inside_interpolated_string() -> None:
    # Given valid multiline Python inside a formatted string, not an IPython magic.
    source = document('text = f"""{(5\n%2\n)}"""\n', False)
    # When upgrading the notebook.
    result = transform(source)
    # Then the string's expression remains byte-exact.
    assert not result.items
    assert result.source == source


def test_magic_preserves_strings_when_data_contains_unicode_line_separators() -> None:
    # Given separators that str.splitlines treats as lines but Python does not.
    code = 'text = """hello\vdata\u2028more\n%pip install daytona\n"""\n'
    source = document(code + "%pip install daytona\n", False)
    expected = document(code + '%pip install "daytona[v0-sdk]>=2,<3"\n', False)
    # When upgrading the real magic following the string.
    result = transform(source)
    # Then token line numbers still address physical Python lines correctly.
    assert not result.items
    assert result.source == expected


@pytest.mark.parametrize("array", [False, True])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_real_magic_upgrades_when_string_and_shell_quotes_precede_it(
    array: bool, ending: str
) -> None:
    # Given opaque magic quotes, escaped-string continuation, and a real installer.
    code = (
        "%time print(''')\n"
        'text = "hello\\\n!pip install daytona==0.224.0"\n'
        'data = """\n%pip install daytona==0.224.0\n?daytona\n"""\n'
        "%pip install daytona==0.224.0\n"
        "from daytona import Daytona\nDaytona().create()\n"
    ).replace("\n", ending)
    source = document(code, array)
    expected_code = code.replace(
        ending + "%pip install daytona==0.224.0" + ending + "from",
        ending + '%pip install "daytona[v0-sdk]>=2,<3"' + ending + "from",
    ).replace("Daytona().create()", "Daytona().v0.create()")
    # When upgrading executable code alongside magic-shaped data.
    result = transform(source)
    # Then only the real magic and client call change, preserving the JSON layout.
    assert not result.items
    assert result.source == document(expected_code, array)


@pytest.mark.parametrize("array", [False, True])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_notebook_removal_preserves_data_when_real_comments_are_present(
    array: bool, ending: str
) -> None:
    # Given the string-removal reproduction inside a notebook with real suggestions.
    code = 'message = """hello\n# daytona-v2: runtime data\nworld"""\n'
    code = code.replace("\n", ending)
    source = document("# daytona-v2: obsolete" + ending + code, array)
    # When removing notebook suggestions.
    result = transform(source, "remove-suggestions")
    # Then only the real comment is removed and string/JSON bytes are preserved.
    assert result.source == document(code, array)


@pytest.mark.parametrize("array", [False, True])
def test_notebook_rerun_preserves_data_when_replacing_stale_suggestions(
    array: bool,
) -> None:
    # Given a stale block, magic-shaped data, tagged string data, and a proven call.
    code = (
        'message = """hello\n# daytona-v2: runtime data\n'
        '%pip install daytona==0.224.0\nworld"""\n'
        "from daytona import Daytona\napi = Daytona()\napi.v0.create()\n"
    )
    source = document("# daytona-v2: obsolete\n" + code, array)
    # When replacing, rerunning, and removing suggestions.
    fresh = transform(source, "suggest-v2").source
    rerun = transform(fresh, "suggest-v2").source
    restored = transform(rerun, "remove-suggestions").source
    # Then only genuine comments were replaced and the round trip preserves data.
    assert fresh == rerun
    assert restored == document(code, array)
    assert "# daytona-v2: obsolete" not in fresh
