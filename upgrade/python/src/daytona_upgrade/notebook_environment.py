# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Parse executable notebook environment setters before magic masking."""

from __future__ import annotations

import re
import shlex

from .environment import VARIABLES, EnvironmentWrite


def magic_environment(line: str, row: int) -> EnvironmentWrite | None:
    """Recognize writes, not `%env` reads; never evaluate interpolation or shell code."""
    setter = re.fullmatch(
        r"\s*(%env|%set_env|!export)\s+([A-Za-z_]\w*)(?:\s*=\s*|\s+)(.*?)\s*",
        line,
    )
    if setter is None or setter[2] not in VARIABLES:
        return None
    value = setter[3]
    literal: str | None = (
        value[1:-1]
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]
        else value
    )
    if any(char in value for char in "$`{}"):
        literal = None
    elif setter[1] == "!export":
        try:
            tokens = shlex.split(value, comments=True)
        except ValueError:
            literal = None
        else:
            literal = (
                tokens[0]
                if len(tokens) == 1 and not re.search(r"[;&|<>()]", value)
                else ""
                if not tokens
                else None
            )
    return EnvironmentWrite("", row, setter.start(2) + 1, literal, setter[2])
