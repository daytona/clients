# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dropping URL keywords preserves the surrounding call's physical layout."""

from __future__ import annotations

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize("position", [0, 1, 2])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("field", ["api_url", "server_url"])
def test_preserves_multiline_layout_when_url_keyword_is_dropped(
    position: int, newline: str, field: str
) -> None:
    # Given a URL field among retained keywords, including a trailing comma.
    lines = ["    api_key='key',", "    organization_id='org',"]
    removed = f"    {field}='https://app.daytona.io/api',   "
    lines.insert(position, removed)
    source = (
        "from daytona import DaytonaConfig\nconfig = DaytonaConfig(\n"
        + "\n".join(lines)
        + "\n)\n"
    ).replace("\n", newline)
    # When migrating a default Cloud URL.
    result = migrate(source)
    # Then the entire keyword line disappears, not the surrounding layout.
    assert result.source == source.replace(removed + newline, "")
    assert result.items == ()


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ("\n    api_url=None,\n", "\n"),
        ("\n    api_url=None\n", "\n"),
        ("api_key='k', api_url=None", "api_key='k'"),
        ("api_key='k', api_url=None,", "api_key='k',"),
        ("api_url=None, api_key='k',", "api_key='k',"),
        (
            "\n    api_key='k',  # keep key\n    api_url=None,  # drop URL\n",
            "\n    api_key='k',  # keep key\n",
        ),
        (
            "\n    api_key='k',\n    api_url=None\n",
            "\n    api_key='k',\n",
        ),
        (
            "\n    api_key='k',\n    api_url=None,\n    server_url=None,\n",
            "\n    api_key='k',\n",
        ),
    ],
)
def test_preserves_delimiters_when_terminal_url_keywords_are_dropped(
    arguments: str, expected: str
) -> None:
    # Given inline, empty, commented, and multiple-URL constructor layouts.
    prefix = "from daytona import DaytonaConfig\nconfig = DaytonaConfig("
    source = prefix + arguments + ")\n"
    # When removing default URL keywords.
    result = migrate(source)
    # Then closing delimiters and retained comments remain in place.
    assert result.source == prefix + expected + ")\n"
    assert result.items == ()


def test_preserves_nested_closing_indent_when_asserted_url_is_dropped() -> None:
    # Given a nested config using the explicit Cloud-usage assertion.
    removed = "        api_url=url,\n"
    source = (
        "from daytona import DaytonaConfig\ndef config():\n"
        "    return DaytonaConfig(\n        api_key='key',\n" + removed + "    )\n"
    )
    # When dropping a nonliteral URL after the operator's assertion.
    result = migrate(source, drop=True)
    # Then the closing paren retains the function body's indentation.
    assert result.source == source.replace(removed, "")
    assert [item["kind"] for item in result.items] == ["config-field"]
