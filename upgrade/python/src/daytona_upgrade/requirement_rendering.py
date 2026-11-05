# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Render validated dependency replacements without treating URL data as markers."""

from __future__ import annotations

import re

from packaging.requirements import InvalidRequirement, Requirement

from .dependency_versions import target_constraint
from .models import Mapping


def render_requirement(
    parsed: re.Match[str], requirement: Requirement, mapping: Mapping
) -> str | None:
    """Keep marker/comment trivia only where the requirement parser recognizes it."""
    tail = parsed["tail"]
    comment = re.search(r"\s+#.*$", tail)
    suffix = comment.group() if comment else tail[len(tail.rstrip()) :]
    if requirement.marker is not None:
        # PEP 508 direct URLs terminate at whitespace, not at URL semicolons.
        marker = re.search(r"\s+;.*$" if requirement.url else r"\s*;.*$", tail)
        if marker is None:
            return None
        suffix = marker.group()
    extras = [
        part.strip()
        for part in (parsed["extras"] or "[]")[1:-1].split(",")
        if part.strip()
    ]
    extras.extend(extra for extra in mapping.extras if extra not in extras)
    target = mapping.package_new
    if extras:
        target += "[" + ",".join(extras) + "]"
    existing = str(requirement.specifier)
    kept = target_constraint(
        requirement.name, existing, mapping.package_new, mapping.specifier
    )
    # A kept constraint keeps its original text (packaging reorders specifiers).
    target += (
        tail[: len(tail) - len(suffix)].strip()
        if existing and kept == existing and kept != mapping.specifier
        else mapping.specifier
    )
    result = parsed["indent"] + target + suffix + (parsed["ending"] or "")
    try:
        Requirement(re.split(r"\s+#", result, maxsplit=1)[0].strip())
    except InvalidRequirement:
        return None
    return result
