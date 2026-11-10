# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Render validated dependency replacements without treating URL data as markers."""

from __future__ import annotations

import re

from packaging.requirements import InvalidRequirement, Requirement

from .dependency_versions import target_constraint
from .models import Mapping


def target_extras(extras: list[str], mapping: Mapping) -> tuple[list[str], list[str]]:
    """The extras a migrated declaration writes (its own that the 2.x
    distribution defines, then the mapping's), and the ones it drops."""
    known = {re.sub(r"[-_.]+", "-", extra).lower() for extra in mapping.known_extras}
    kept = [extra for extra in extras if re.sub(r"[-_.]+", "-", extra).lower() in known]
    dropped = [extra for extra in extras if extra not in kept]
    kept.extend(extra for extra in mapping.extras if extra not in kept)
    return kept, dropped


def dropped_extra_message(extra: str, name: str, mapping: Mapping) -> str:
    return (
        f"Dropped the {extra} extra of {name}: SDK 2.x's {mapping.package_new} "
        f"has no such extra (its extras: {', '.join(mapping.known_extras)}); "
        "install what it provided directly if you still need it."
    )


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
    extras, _ = target_extras(
        [
            part.strip()
            for part in (parsed["extras"] or "[]")[1:-1].split(",")
            if part.strip()
        ],
        mapping,
    )
    target = mapping.package_new
    if extras:
        target += "[" + ",".join(extras) + "]"
    existing = str(requirement.specifier)
    kept = target_constraint(
        requirement.name, existing, mapping.package_new, mapping.specifier
    )
    # A kept constraint keeps its original text (packaging reorders specifiers);
    # otherwise the target, or a pre-release range's own pinned floor.
    target += (
        tail[: len(tail) - len(suffix)].strip()
        if existing and kept == existing and kept != mapping.specifier
        else kept
    )
    result = parsed["indent"] + target + suffix + (parsed["ending"] or "")
    try:
        Requirement(re.split(r"\s+#", result, maxsplit=1)[0].strip())
    except InvalidRequirement:
        return None
    return result
