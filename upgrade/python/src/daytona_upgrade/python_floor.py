# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Report project interpreter ranges incompatible with the SDK 2.x floor."""

from __future__ import annotations

import re
from typing import Final

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

PYTHON_REVIEW: Final = "SDK 2.x needs Python 3.10+; restrict this project's Python range before installing the upgraded dependency."


def needs_python_review(constraint: str) -> bool:
    """Find an admitted stable interpreter below 3.10, or an unparsed range."""
    for alternative in constraint.split("||"):
        # Poetry caret/tilde bounds have the same lower endpoint; their upper
        # endpoint cannot exclude an older witness admitted by that endpoint.
        normalized = re.sub(
            r"[\^~](?!=)\s*(\d+(?:\.\d+)*)", r">=\1", alternative.strip()
        )
        if normalized == "*":
            normalized = ""
        elif re.fullmatch(r"\d+(?:\.[\d*]+)*", normalized):
            normalized = "==" + normalized
        try:
            specifiers = SpecifierSet(normalized)
        except InvalidSpecifier:
            return True
        # Membership changes only at the declared endpoints or their adjacent
        # stable patch releases; include each minor's baseline for wildcard gaps.
        candidates = {
            Version(f"{major}.{minor}.{patch}")
            for major in (2, 3)
            for minor in range(10)
            for patch in (0, 1)
        }
        for bound in re.findall(r"\d+(?:\.\d+)+", normalized):
            version = Version(bound)
            release = (*version.release, 0, 0)[:3]
            major, minor, patch = release
            candidates.update((version, Version(f"{major}.{minor}.{patch + 1}")))
        if any(
            version < Version("3.10") and version in specifiers
            for version in candidates
        ):
            return True
    return False
