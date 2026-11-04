# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Resolver-only dependency policies never certify a runtime installation."""

from __future__ import annotations

from dataclasses import replace

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Array, InlineTable, String, Table

from ._dependencies import _REQUIREMENT, Migration, _normalized, _string


def _manual(migration: Migration, setting: str, declaration: str) -> None:
    target = migration.mapping.package_new + migration.mapping.specifier
    message = (
        f"Review {setting} for {declaration} manually and set it to {target}; "
        "the original resolver declaration was preserved."
    )
    migration.manual.append((0, message))
    migration.resolver_review.append((0, message))


def _record(migration: Migration, setting: str, change: tuple[str, str]) -> None:
    before, after = change
    if before != after:
        migration.changes.append(change)
        target = migration.mapping.package_new + migration.mapping.specifier
        migration.resolver_review.append(
            (
                0,
                f"Review {setting} for {before} in this ancestor manifest and set it to {target} manually; ancestor manifests are never edited.",
            )
        )


def resolver_array(value: Array, migration: Migration, setting: str) -> None:
    """Rewrite registry overrides; preserve nontrivial constraint policies for review."""
    target = migration.mapping.package_new + migration.mapping.specifier
    extras = ("v0-sdk",) if not setting.endswith("constraint-dependencies") else ()
    resolver = Migration(replace(migration.mapping, extras=extras, requirement=target))
    resolver.certify = False
    for index, entry in enumerate(value):
        if not isinstance(entry, String):
            continue
        source = str(entry)
        parsed = _REQUIREMENT.fullmatch(source)
        if parsed is None or _normalized(parsed["name"]) not in migration.names:
            continue
        migration.found = True
        try:
            requirement = Requirement(source)
        except (InvalidRequirement, InvalidSpecifier):
            # Older packaging raises InvalidSpecifier for malformed specifiers.
            _manual(migration, setting, source)
            continue
        if requirement.url or (
            setting.endswith("constraint-dependencies")
            and requirement.specifier
            and requirement.specifier != SpecifierSet(migration.mapping.specifier)
        ):
            _manual(migration, setting, source)
            continue
        # Overrides keep the user's extras (uv installs the override in place of
        # the requirement) and gain v0-sdk; constraints cannot carry extras.
        start, end = parsed.span("extras")
        keep_extras = start < 0 or not setting.endswith("constraint-dependencies")
        rewritten = resolver.requirement(
            source if keep_extras else source[:start] + source[end:]
        )
        _record(migration, setting, (source, rewritten))
        if rewritten != source:
            value[index] = _string(entry, rewritten)


def pdm_overrides(
    table: Table | InlineTable | OutOfOrderTableProxy, migration: Migration
) -> None:
    """Migrate unambiguous PDM registry overrides without dependency extras."""
    setting = "tool.pdm.resolution.overrides"
    names = [name for name in table if _normalized(str(name)) in migration.names]
    if not names:
        return
    migration.found = True
    if len(names) > 1:
        _manual(migration, setting, ", ".join(str(name) for name in names))
        return
    name = names[0]
    original = table[name]
    if not isinstance(original, String):
        _manual(migration, setting, str(name))
        return
    value = str(original)
    try:
        Version(value)
    except InvalidVersion:
        version = value
    else:
        version = "==" + value
    try:
        SpecifierSet(version)
    except InvalidSpecifier:
        _manual(migration, setting, str(name))
        return
    updated = _string(original, migration.mapping.specifier)
    updated.trivia.comment = original.trivia.comment
    updated.trivia.comment_ws = original.trivia.comment_ws
    if str(name) != migration.mapping.package_new:
        del table[name]
    table[migration.mapping.package_new] = updated
    _record(
        migration,
        setting,
        (
            f"{name} = {original.as_string()}",
            f"{migration.mapping.package_new} = {updated.as_string()}",
        ),
    )
