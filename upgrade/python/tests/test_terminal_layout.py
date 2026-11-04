# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Retain comments and adjacent wildcard pairs across repeated migration."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize("comma", ["", ","])
def test_preserves_standalone_comment_when_terminal_url_keyword_is_removed(
    comma: str,
) -> None:
    # Given comments owned by both the retained comma and the closing delimiter.
    removed = f"    api_url=None{comma}\n"
    source = (
        "from daytona import DaytonaConfig\nconfig = DaytonaConfig(\n"
        "    api_key='key',\n    # before URL\n" + removed + "    # after URL\n)\n"
    )
    # When removing the obsolete URL keyword.
    result = migrate(source)
    # Then standalone comments survive in their original order.
    assert result.source == source.replace(removed, "")


@pytest.mark.parametrize("retained", ["", "    api_key='key',\n"])
def test_preserves_closing_comment_when_url_keyword_precedes_retained_arguments(
    retained: str,
) -> None:
    # Given either an emptied call or a retained terminal argument with a comment.
    removed = "    api_url=None,\n"
    source = (
        "from daytona import DaytonaConfig\nconfig = DaytonaConfig(\n"
        + removed
        + retained
        + "    # closing comment\n)\n"
    )
    # When removing the URL keyword.
    result = migrate(source)
    # Then the closing comment remains exactly once, even without retained args.
    assert result.source == source.replace(removed, "")


@pytest.mark.parametrize("separator", ["\n", "; "])
def test_is_idempotent_when_presplit_v0_wildcard_has_trailing_statement(
    separator: str,
) -> None:
    # Given an already split import whose v0 line contains another statement.
    source = f"from daytona import *{separator}from daytona.v0 import *; x = 1\n"
    # When upgrading again.
    result = migrate(source)
    # Then the existing wildcard pair is not duplicated.
    assert result.source == source
