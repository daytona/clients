# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency migration state shared by supported manifest adapters."""

from __future__ import annotations

import re
from typing import Final

import tomlkit
from packaging.requirements import InvalidRequirement, Requirement
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Array, InlineTable, String, Table

from .dependency_versions import requires_v2, target_constraint
from .models import Mapping
from .requirement_rendering import render_requirement
from .toml_values import render_declaration as _render
from .toml_values import toml_string as _string

_REQUIREMENT: Final = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\[[^\]\r\n]*\])?(?P<tail>[^\r\n]*)(?P<ending>\r?\n)?$"
)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class Migration:
    """Mutable accumulator of dependency edits, recognition and manual diagnostics."""

    def __init__(self, mapping: Mapping) -> None:
        self.mapping = mapping
        self.changes: list[tuple[str, str]] = []
        self.manual: list[tuple[int, str]] = []
        self.resolver_review: list[tuple[int, str]] = []
        self.line = 0
        self.found = False
        self.v2_found = False
        self.certify = True
        self.names = {_normalized(name) for name in mapping.package_old}
        self.names.add(_normalized(mapping.package_new))
        self.renamed: set[str] = set()
        self.newline = "\n"
        # Each Daytona declaration's package name and version constraint ("" for none).
        self.declared: list[tuple[str, str]] = []

    def manual_setup(self, line: int, message: str) -> None:
        """Retain uncertain setup declarations as explicit manual-review items."""
        self.manual.append((line, message))

    def requirement(self, source: str) -> str:
        logical = re.sub(r"\\\r?\n", "", source)
        parsed = _REQUIREMENT.fullmatch(logical)
        if parsed is None or _normalized(parsed["name"]) not in self.names:
            egg = re.search(
                r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)",
                re.split(r"\s+#", logical, maxsplit=1)[0],
                re.I,
            )
            if (
                egg is not None
                and _normalized(egg[1]) in self.names
                and not logical.lstrip().startswith("#")
            ):
                self.unparsed_requirement()
            return source
        tail = parsed["tail"]
        hash_locked = bool(re.search(r"(?:^|\s)--hash(?:=|\s)", tail))
        if tail.strip() and tail.lstrip()[0] not in "<>=!~@;#(\\" and not hash_locked:
            self.unparsed_requirement()
            return source
        self.found = True
        self._declare(parsed["name"], logical)
        if "\\" in source or hash_locked:
            self.manual.append(
                (
                    self.line,
                    f"Change this dependency to {self.mapping.requirement} manually and regenerate hashes; the original hash-locked or continued record was preserved.",
                )
            )
            return source
        try:
            requirement = Requirement(re.split(r"\s+#", logical, maxsplit=1)[0].strip())
        except InvalidRequirement:
            self.unparsed_requirement()
            return source
        self.v2_found |= (
            self.certify
            and requirement.marker is None
            and _normalized(requirement.name) == _normalized(self.mapping.package_new)
            and not requirement.url
            and requires_v2(str(requirement.specifier), self.mapping.specifier)
            and {_normalized(extra) for extra in self.mapping.extras}
            <= {_normalized(extra) for extra in requirement.extras}
        )
        result = render_requirement(parsed, requirement, self.mapping)
        if result is None:
            self.unparsed_requirement()
            return source
        if result != source:
            self.changes.append((source.strip(), result.strip()))
        return result

    def _declare(self, name: str, logical: str) -> None:
        """Record the version constraint, ignoring hashes, comments and options."""
        line = re.split(r"\s+(?:#|--)", logical, maxsplit=1)[0].strip()
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            specifier = ""
        else:
            # A marker-gated line may never install: it is no version evidence.
            specifier = "" if requirement.marker else str(requirement.specifier)
        self.declared.append((name, specifier))

    def unparsed_requirement(self) -> None:
        self.found = True
        self.manual.append(
            (
                self.line,
                f"Change this unsupported Daytona dependency to {self.mapping.requirement} manually; its original declaration was preserved.",
            )
        )

    def array(self, value: Array) -> None:
        for index, entry in enumerate(value):
            if isinstance(entry, String):
                rewritten = self.requirement(str(entry))
                if rewritten != str(entry):
                    value[index] = _string(entry, rewritten)

    def _table(
        self, updated: Table | InlineTable | OutOfOrderTableProxy, name: str
    ) -> None:
        current = updated.get("version")
        keep = isinstance(current, String) and not any(
            field in updated for field in ("git", "url", "path")
        )
        for field in ("git", "url", "path", "rev", "branch", "tag", "develop"):
            if field in updated:
                del updated[field]
        version = target_constraint(
            name,
            str(current) if keep else "",
            self.mapping.package_new,
            self.mapping.specifier,
        )
        if version != str(current) or not keep:
            updated["version"] = version
        extras = updated.get("extras")
        if isinstance(extras, Array):
            for extra in self.mapping.extras:
                if extra not in extras:
                    extras.append(extra)
        else:
            added = tomlkit.array()
            added.extend(self.mapping.extras)
            if isinstance(updated, InlineTable) and updated:
                added.trivia.indent = " "
            if not isinstance(updated, InlineTable):
                added.trivia.trail = self.newline
            updated["extras"] = added

    def references(self, table: Table | InlineTable | OutOfOrderTableProxy) -> bool:
        """Follow successful dependency key renames through Poetry extras."""
        if not self.renamed:
            return True
        arrays = [value for value in table.values() if isinstance(value, Array)]
        if len(arrays) != len(table) or any(
            not isinstance(entry, String)
            or re.fullmatch(r"[A-Za-z0-9._-]+", str(entry)) is None
            for value in arrays
            for entry in value
        ):
            self.manual.append(
                (
                    0,
                    "Review Poetry extras references manually before renaming the Daytona dependency; this manifest was preserved.",
                )
            )
            return False
        for value in arrays:
            for index, entry in enumerate(value):
                if (
                    isinstance(entry, String)
                    and _normalized(str(entry)) in self.renamed
                ):
                    value[index] = _string(entry, self.mapping.package_new)
        return True

    def dependencies(self, table: Table | InlineTable | OutOfOrderTableProxy) -> None:
        # Python 3.9 compatibility precludes structural pattern matching.
        declarations = [name for name in table if _normalized(str(name)) in self.names]
        if len(declarations) > 1:
            self.found = True
            self.manual.append(
                (
                    0,
                    f"Consolidate {', '.join(str(name) for name in declarations)} into {self.mapping.requirement} manually; colliding declarations were preserved with their conditions and extras.",
                )
            )
            return
        for name in list(table):
            if _normalized(str(name)) not in self.names:
                continue
            self.found = True
            original = table[name]
            versions = [original] if not isinstance(original, Array) else list(original)
            for entry in versions:
                version = (
                    entry
                    if isinstance(entry, String)
                    else entry.get("version")
                    if isinstance(entry, (Table, InlineTable, OutOfOrderTableProxy))
                    else None
                )
                self.declared.append(
                    (str(name), str(version) if isinstance(version, String) else "")
                )
            if any(
                isinstance(entry, (Table, InlineTable, OutOfOrderTableProxy))
                and "subdirectory" in entry
                for entry in versions
            ):
                self.manual.append(
                    (
                        0,
                        f"Change {name} to {self.mapping.requirement} manually; the source dependency with subdirectory and all its alternatives were preserved.",
                    )
                )
                continue
            self.v2_found |= (
                self.certify
                and _normalized(str(name)) == _normalized(self.mapping.package_new)
                and bool(versions)
                and all(
                    requires_v2(str(entry), self.mapping.specifier)
                    and not self.mapping.extras
                    if isinstance(entry, String)
                    else isinstance(entry, (Table, InlineTable, OutOfOrderTableProxy))
                    # Unknown fields may be environment selectors; only an
                    # unconditional registry requirement is certifying evidence.
                    and set(entry)
                    <= {"version", "extras", "index", "source", "allow-prereleases"}
                    and isinstance(entry.get("version"), String)
                    and requires_v2(str(entry["version"]), self.mapping.specifier)
                    and isinstance(extras := entry.get("extras"), Array)
                    and {_normalized(extra) for extra in self.mapping.extras}
                    <= {_normalized(str(extra)) for extra in extras}
                    for entry in versions
                )
            )
            before = _render(original)
            if isinstance(original, Array):
                alternatives = [
                    entry
                    for entry in original
                    if isinstance(entry, (Table, InlineTable, OutOfOrderTableProxy))
                ]
                if not original or len(alternatives) != len(original):
                    self.manual.append(
                        (
                            0,
                            f"Change {name} to {self.mapping.requirement} manually, preserving each alternative's conditions; unsupported dependency alternatives were preserved.",
                        )
                    )
                if not alternatives:
                    continue
                for entry in alternatives:
                    self._table(entry, str(name))
                updated = original
            elif isinstance(original, (Table, InlineTable, OutOfOrderTableProxy)):
                self._table(original, str(name))
                updated = original
            elif isinstance(original, String):
                updated = tomlkit.inline_table()
                updated["version"] = target_constraint(
                    str(name),
                    str(original),
                    self.mapping.package_new,
                    self.mapping.specifier,
                )
                updated["extras"] = list(self.mapping.extras)
                updated.trivia.comment = original.trivia.comment
                updated.trivia.comment_ws = original.trivia.comment_ws
            else:
                self.manual.append(
                    (
                        0,
                        f"Change {name} to {self.mapping.requirement} manually; the unsupported dependency value was preserved.",
                    )
                )
                continue
            if str(name) != self.mapping.package_new:
                del table[name]
                self.renamed.add(_normalized(str(name)))
            if updated is not original or str(name) != self.mapping.package_new:
                table[self.mapping.package_new] = updated
            if str(name) != self.mapping.package_new or before != _render(updated):
                self.changes.append(
                    (
                        f"{name} = {before}",
                        f"{self.mapping.package_new} = {_render(updated)}",
                    )
                )
