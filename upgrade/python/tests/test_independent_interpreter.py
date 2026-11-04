# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Interpreter limitations are reported as tool compatibility, not bad source."""

from __future__ import annotations

import ast
import sys

import pytest

from daytona_upgrade.flow import SourceContext
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import ProjectIndex
from daytona_upgrade.transform import transform_source


def test_explains_runtime_when_interpreter_rejects_newer_syntax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given valid Python 3.10 syntax and the real parser's 3.9 grammar.
    source = "from daytona import Daytona\nmatch value:\n case 1: Daytona().create()\n"
    parser = ast.parse

    def parse_as_python39(source: str) -> ast.Module:
        return parser(source, feature_version=(3, 9))

    context = SourceContext("app.py", "app", ProjectIndex({}))
    # When the flow parser cannot support syntax that LibCST accepts.
    with monkeypatch.context() as patch:
        if sys.version_info >= (3, 10):
            patch.setattr(ast, "parse", parse_as_python39)
        result = transform_source(source, TransformOptions(load_mapping()), context)
    # Then users get a runtime remedy without any source edits.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["unsupported-file"]
    message = result.items[0]["message"]
    assert (
        f"tool is running on Python {sys.version_info.major}.{sys.version_info.minor}"
        in message
    )
    assert "Python >=3.10" in message
    assert "Cannot parse Python source" not in message


def test_reports_source_error_when_syntax_is_malformed() -> None:
    # Given genuinely invalid Python, not an interpreter-version limitation.
    source = "from daytona import Daytona\nunfinished = (\n"
    context = SourceContext("app.py", "app", ProjectIndex({}))
    # When neither source parser can accept the file.
    result = transform_source(source, TransformOptions(load_mapping()), context)
    # Then the source error is retained rather than promising a version remedy.
    assert result.items[0]["message"].startswith("Could not be parsed:")
    assert result.source == source
