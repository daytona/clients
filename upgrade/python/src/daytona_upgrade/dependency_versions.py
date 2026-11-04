# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative lower-bound proof for already upgraded dependency declarations."""

from __future__ import annotations

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


def requires_v2(constraint: str) -> bool:
    """Require an explicit 2.x-or-newer lower bound in every Poetry alternative."""
    for alternative in constraint.split("||"):
        normalized = re.sub(
            r"[\^~](?!=)\s*(\d+(?:\.\d+)*)", r">=\1", alternative.strip()
        )
        if re.fullmatch(r"\d+(?:\.[\d*]+)*", normalized):
            normalized = "==" + normalized
        try:
            specifiers = SpecifierSet(normalized)
            bounded = any(
                specifier.operator in {">=", ">", "==", "~=", "==="}
                and Version(specifier.version.removesuffix(".*")) >= Version("2")
                for specifier in specifiers
            )
        except (InvalidSpecifier, InvalidVersion):
            return False
        if not bounded:
            return False
    return True
