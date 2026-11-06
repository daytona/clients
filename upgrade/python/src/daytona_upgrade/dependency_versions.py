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
            # The least version above it, as semver's minVersion reads `>`: the
            # next patch, or just above a pre-release (2.0.0a8 -> 2.0.0a8.post0).
            if bound.is_prerelease:
                bound = Version(f"{bound}.post0")
            else:
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


def _admits(constraint: str, version: Version) -> bool:
    """Whether a constraint admits a version: a Poetry ``||`` union when any
    alternative does (other Poetry syntax: assumed)."""
    alternatives = constraint.split("||")
    if len(alternatives) > 1:
        return any(_admits(alternative, version) for alternative in alternatives)
    try:
        return SpecifierSet(constraint.strip()).contains(version, prereleases=True)
    except InvalidSpecifier:
        return True


def _exclusive_bound(constraint: str) -> Version | None:
    """The version of a single ``>`` bound, if the constraint has one."""
    try:
        bounds = [
            entry for entry in SpecifierSet(constraint.strip()) if entry.operator == ">"
        ]
    except InvalidSpecifier:
        return None
    try:
        return Version(bounds[0].version) if len(bounds) == 1 else None
    except InvalidVersion:
        return None


def _exact(constraint: str) -> bool:
    """A single ``==`` / ``===`` pin, or a bare Poetry version."""
    text = constraint.strip()
    try:
        Version(text)
        return True
    except InvalidVersion:
        pass
    try:
        specifiers = list(SpecifierSet(text))
    except InvalidSpecifier:
        return False
    return (
        len(specifiers) == 1
        and specifiers[0].operator in {"==", "==="}
        and "*" not in specifiers[0].version
    )


def _floor_alternatives(constraint: str, floor: Version) -> list[str]:
    """The ``||`` alternatives whose lower bound is the constraint's floor."""
    return [
        alternative
        for alternative in constraint.split("||")
        if lower_bound(alternative) == floor
    ] or [constraint]


def pinned_floor(constraint: str, target: str) -> str | None:
    """The exact pin to write for a pre-release range at or above a pre-release
    target (">=2.0.0a9" under ==2.0.0a8 -> "==2.0.0a9"): never below the range's
    own floor. None otherwise."""
    floor, wanted = lower_bound(constraint), lower_bound(target)
    if (
        floor is None
        or wanted is None
        or not wanted.is_prerelease
        or not floor.is_prerelease
        or _stale_for_target(floor, wanted)
        or _exact(constraint)
        # An exclusive bound on every alternative with this floor names no release.
        or all(
            ">" in alternative.replace(">=", "")
            for alternative in _floor_alternatives(constraint, floor)
        )
    ):
        return None
    if floor.major != wanted.major or floor < wanted:
        return None
    # A floor the range excludes (">=2.0.0a9,!=2.0.0a9") names no release to
    # pin: keep the range rather than fall back to an older target.
    return f"=={floor}" if _admits(constraint, floor) else constraint


def _stale_for_target(floor: Version, wanted: Version) -> bool:
    """While the target is an alpha, the published 2.0.0rc1 is not ahead of it:
    a stale pre-alpha build without the v0 namespace. Any other beta or rc is."""
    return (
        wanted.pre is not None and wanted.pre[0] == "a" and floor == Version("2.0.0rc1")
    )


def at_or_above_target(constraint: str, target: str) -> bool:
    """A constraint already at or above the target in the target's major
    (``==2.0.0a9`` for a ``==2.0.0a8`` tool): left as it is, so an older tool
    release never downgrades a project."""
    alternatives = constraint.split("||")
    if len(alternatives) > 1:
        # A union is at or above the target only when every alternative is (a
        # later major counts: it is never downgraded either).
        return all(
            at_or_above_target(alternative.strip(), target)
            or above_target_major(alternative.strip(), target)
            for alternative in alternatives
        )
    floor, wanted = lower_bound(constraint), lower_bound(target)
    exclusive = _exclusive_bound(constraint)
    if (
        exclusive is not None
        and wanted is not None
        and exclusive.major == wanted.major
        and exclusive >= wanted
    ):
        # An exclusive bound at or above the target admits only later releases:
        # kept, never rewritten to a version it excludes.
        return True
    if floor is not None and not _admits(constraint, floor):
        # The synthetic floor of `>` is outside the range (`>2.0.0,<2.0.1`):
        # the range does not reach the target.
        return False
    if (
        floor is not None
        and wanted is not None
        and wanted.is_prerelease
        and floor.is_prerelease
        and not _exact(constraint)
    ):
        # A pre-release range (">=2.0.0a8") also admits the stale 2.0.0rc1.
        return False
    return (
        floor is not None
        and wanted is not None
        and floor.major == wanted.major
        and floor >= wanted
        and not _stale_for_target(floor, wanted)
        # While the target is a pre-release, a range floored on that same
        # release's stable version (">=2,<3" under ==2.0.0a8, what pre-lockstep
        # releases wrote) installs nothing yet; an exact ==2.0.0 pin or any
        # later stable floor is a project ahead of this tool.
        and (
            not wanted.is_prerelease
            or floor.is_prerelease
            or _exact(constraint)
            or (*floor.release, 0, 0)[:3] != (*wanted.release, 0, 0)[:3]
        )
    )


def above_target_major(constraint: str, target: str) -> bool:
    """A constraint whose floor is in a later major than the target's."""
    floor, wanted = lower_bound(constraint), lower_bound(target)
    return floor is not None and wanted is not None and floor.major > wanted.major


def target_constraint(name: str, constraint: str, package: str, target: str) -> str:
    """The constraint to write for a Daytona declaration: the existing one when
    it already names the new package at or above the target (or in a later
    major, which is never downgraded), else the target."""
    keep = re.sub(r"[-_.]+", "-", name).lower() == re.sub(
        r"[-_.]+", "-", package
    ).lower() and (
        at_or_above_target(constraint, target) or above_target_major(constraint, target)
    )
    if keep:
        return constraint
    pinned = pinned_floor(constraint, target)
    return pinned if pinned is not None else target


def requires_v2(constraint: str, target: str | None = None) -> bool:
    """Require an explicit 2.x-or-newer lower bound in every Poetry alternative.

    A constraint at or above ``target`` (the specifier this tool writes) also
    counts, so a pinned pre-release certifies; pre-release floors below it do not.
    """
    if target is not None:
        # Only the target or a later release of its major certifies: a stable
        # floor under a pre-release target (">=2,<3") installs nothing yet.
        return at_or_above_target(constraint, target)
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
