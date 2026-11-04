# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Formatting-preserving TOML scalar construction and declaration rendering."""

from __future__ import annotations

from copy import deepcopy

import tomlkit
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Item, String


def toml_string(original: String, value: str) -> String:
    """Retain TOML string quoting and surrounding array trivia."""
    rendered = original.as_string()
    replacement = tomlkit.string(
        value,
        literal=rendered.startswith("'"),
        multiline=rendered.startswith(('"""', "'''")),
    )
    replacement.trivia.indent = original.trivia.indent
    replacement.trivia.comment_ws = original.trivia.comment_ws
    replacement.trivia.comment = original.trivia.comment
    replacement.trivia.trail = original.trivia.trail
    return replacement


def render_declaration(value: Item | OutOfOrderTableProxy | bool) -> str:
    """Proxies lack Item rendering; booleans are unwrapped by table lookup."""
    if isinstance(value, bool):
        return tomlkit.item(value).as_string()
    return (
        tomlkit.dumps(deepcopy(dict(value)))
        if isinstance(value, OutOfOrderTableProxy)
        else value.as_string()
    )
