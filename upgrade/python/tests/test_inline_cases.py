# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Inline match cases retain their migration annotations."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import Mode, SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def transform(source: str, mode: Mode) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, mode),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match syntax needs Python 3.10")
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("mode", ["upgrade", "suggest-v2"])
def test_case_gets_annotations_when_its_body_is_inline(mode: Mode, ending: str) -> None:
    # Given inline cases requiring a FIXME or v2 guidance, with a user comment.
    setup = "daytona = Daytona()\n" if mode == "suggest-v2" else ""
    source = (
        "from daytona import Daytona\n"
        + setup
        + 'match value:\n    # user note\n    case 1: daytona.start("a")\n    case _: pass\n'
    ).replace("\n", ending)
    # When generating case-owned comments.
    result = transform(source, mode)
    # Then comments attach to that case without changing executable syntax.
    marker = "# daytona-v2:" if mode == "suggest-v2" else "# FIXME(daytona-upgrade):"
    assert result.items
    assert any(line.startswith("    " + marker) for line in result.source.splitlines())
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert result.source.count("# user note") == 1
    assert transform(result.source, mode).source == result.source
    if mode == "suggest-v2":
        assert transform(result.source, "remove-suggestions").source == source


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match syntax needs Python 3.10")
def test_cli_writes_guidance_when_case_body_is_inline(tmp_path: Path) -> None:
    # Given an inline match case in an actual project.
    app = tmp_path / "app.py"
    source = (
        "from daytona import Daytona\nd = Daytona()\n"
        'match value:\n    case 1: d.start("a")\n'
    )
    app.write_text(source)
    # When the real CLI writes suggestions.
    status = cli.main([str(tmp_path), "--suggest-v2", "--force", "--yes"])
    # Then the on-disk case has guidance and executable behavior is unchanged.
    assert status == 0
    migrated = app.read_text()
    assert "    # daytona-v2:" in migrated
    assert ast.dump(ast.parse(migrated)) == ast.dump(ast.parse(source))
