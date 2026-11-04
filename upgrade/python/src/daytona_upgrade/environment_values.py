# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal endpoint evidence, including bounded shell-style default expressions."""

from __future__ import annotations

import re


def endpoint_value(value: str) -> str | None:
    """Inspect defaults without trusting the process environment or error messages."""
    expression = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z_0-9]*(?::?([-+?])(.*))?\}", value)
    if expression:
        # Required-variable messages are not endpoint values. Both fallback and
        # alternate words can be selected at runtime, so inspect their evidence.
        if expression[1] not in {"-", "+"} or not expression[2]:
            return None
        value = expression[2]
    return None if any(marker in value for marker in ("$", "{{", "{%")) else value
