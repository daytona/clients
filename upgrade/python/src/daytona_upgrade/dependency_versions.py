# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative lower-bound proof for already upgraded dependency declarations."""

from __future__ import annotations

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


def _single_floor(specifier: str) -> Version | None:
    try:
        specifiers = SpecifierSet(specifier)
    except InvalidSpecifier:
        return None
    bounds: list[Version] = []
    for entry in specifiers:
        if entry.operator not in {"==", "===", ">=", "~=", ">"}:
            continue
        try:
            bound = Version(entry.version.removesuffix(".*"))
        except InvalidVersion:
            continue
        if entry.operator == ">":
            # The least version above it, as semver's minVersion reads `>`.
            major, minor, micro = (*bound.release, 0, 0)[:3]
            bound = Version(f"{major}.{minor}.{micro + 1}")
        bounds.append(bound)
    return max(bounds) if bounds else None


def lower_bound(constraint: str) -> Version | None:
    """The least version a constraint admits: PEP 440 and Poetry caret/tilde,
    bare versions and ``||`` alternatives (the least alternative's bound);
    None when an alternative has no lower bound."""
    bounds: list[Version] = []
    for alternative in constraint.split("||"):
        normalized = re.sub(
            r"[\^~](?!=)\s*(\d+(?:\.\d+)*)", r">=\1", alternative.strip()
        )
        if re.fullmatch(r"\d+(?:\.[\d*]+)*", normalized):
            normalized = "==" + normalized
        else:
            try:  # A bare pre-release (Poetry "2.0.0a9") pins that version too.
                normalized = "==" + str(Version(normalized))
            except InvalidVersion:
                pass
        bound = _single_floor(normalized)
        if bound is None:
            return None
        bounds.append(bound)
    return min(bounds) if bounds else None


def at_or_above_target(constraint: str, target: str) -> bool:
    """A constraint already at or above the target in the target's major
    (``==2.0.0a9`` for a ``==2.0.0a8`` tool): left as it is, so an older tool
    release never downgrades a project."""
    floor, wanted = lower_bound(constraint), lower_bound(target)
    return (
        floor is not None
        and wanted is not None
        and floor.major == wanted.major
        and floor >= wanted
        # While the target is a pre-release, a floor on an unreleased stable
        # version (">=2,<3", what pre-lockstep releases wrote) installs nothing.
        and (not wanted.is_prerelease or floor.is_prerelease)
    )


def target_constraint(name: str, constraint: str, package: str, target: str) -> str:
    """The constraint to write for a Daytona declaration: the existing one when
    it already names the new package at or above the target, else the target."""
    keep = re.sub(r"[-_.]+", "-", name).lower() == re.sub(
        r"[-_.]+", "-", package
    ).lower() and at_or_above_target(constraint, target)
    return constraint if keep else target


def requires_v2(constraint: str, target: str | None = None) -> bool:
    """Require an explicit 2.x-or-newer lower bound in every Poetry alternative.

    A constraint at or above ``target`` (the specifier this tool writes) also
    counts, so a pinned pre-release certifies; pre-release floors below it do not.
    """
    if target is not None and at_or_above_target(constraint, target):
        return True
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
