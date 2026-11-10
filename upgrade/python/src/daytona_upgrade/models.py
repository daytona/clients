# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Typed mapping and report boundary models (Python 3.9 compatible)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

Mode = Literal["upgrade", "suggest-v2", "remove-suggestions"]
Category = Literal["direct", "differs", "none"]
ItemKind = Literal[
    "fixme",
    "config-field",
    "deprecated-target",
    "custom-api-url",
    "env-api-url",
    "private-import",
    "dynamic-import",
    "mock-target",
    "peer-dependency",
    "unsupported-file",
    "types-unresolved",
    "suggestion",
    "workflow",
    "other-ecosystem",
    "manual-dependency",
]


class SuggestionReport(TypedDict):
    id: str
    receiver: str
    v0: str
    v2: str | None
    category: Category
    notes: list[str]
    docs: str


class ItemBase(TypedDict):
    file: str
    line: int
    column: int
    kind: ItemKind
    message: str


class Item(ItemBase, total=False):
    suggestion: SuggestionReport


@dataclass(frozen=True)
class Receiver:
    types: tuple[str, ...]
    members: tuple[str, ...]
    parent: str | None
    attr: str | None
    produced_by: tuple[str, ...]


@dataclass(frozen=True)
class Suggestion:
    id: str
    receiver: str
    method: str | None
    form: str | None
    category: Category
    notes: tuple[str, ...]
    anchor: str
    starts_workflow: bool


@dataclass(frozen=True)
class Mapping:
    root_names: tuple[str, ...]
    moved: tuple[str, ...]
    config: dict[str, str]
    cloud_urls: tuple[str, ...]
    package_old: tuple[str, ...]
    package_new: str
    requirement: str
    specifier: str
    extras: tuple[str, ...]
    modules: tuple[str, ...]
    docs: str
    receivers: dict[str, Receiver]
    suggestions: tuple[Suggestion, ...]
    data_traps: tuple[str, ...]
    v0_exports: tuple[str, ...] | None = None
    # The pinned 0.x release SDK 2.x bundles as .v0.
    v0_version: str | None = None
    # 0.x root error aliases the 2.x root dropped -> the shared category each named.
    root_alias_categories: dict[str, str] = field(default_factory=dict)
    # Every extra the 2.x distribution defines; others are dropped on migration.
    known_extras: tuple[str, ...] = ()

    @property
    def root_imports(self) -> tuple[str, ...]:
        """Shared errors retain their 0.x behavior only in the v0 namespace."""
        return tuple(name for name in self.root_names if not name.endswith("Error"))


def load_mapping(path: Path | None = None, tool_version: str | None = None) -> Mapping:
    """Read the bundled, schema-checked shared mapping into domain values.

    The dependency target follows this tool's own version (``sdk_target``);
    ``tool_version`` overrides it for tests.
    """
    from . import __version__
    from .sdk_target import target_specifier

    # JSON is decoded only here; no untyped values escape this boundary.
    raw = json.loads((path or Path(__file__).with_name("mapping.json")).read_text())
    package = raw["packages"]["py"]
    specifier = target_specifier(
        __version__ if tool_version is None else tool_version, package["specifier"]
    )
    requirement = (
        package["requirement"]
        if specifier == package["specifier"]
        else package["new"]
        + (f"[{','.join(package['extras'])}]" if package["extras"] else "")
        + specifier
    )
    exports = raw.get("v0Exports", {}).get("py")
    receivers = {
        key: Receiver(
            tuple(value["py"]["types"]),
            tuple(value["py"]["members"]),
            value.get("parent"),
            value.get("attr", {}).get("py"),
            tuple(value.get("producedBy", [])),
        )
        for key, value in raw["receivers"].items()
    }
    suggestions = tuple(
        Suggestion(
            entry["id"],
            entry["receiver"],
            entry["v0"]["py"],
            entry["v2"]["py"],
            # A language without a v2 form has no equivalent there.
            "none" if entry["v2"]["py"] is None else entry["category"],
            tuple(entry["notes"]["py"]),
            entry["anchor"],
            entry.get("startsWorkflow", False),
        )
        for entry in raw["suggestions"]
    )
    return Mapping(
        tuple(raw["rootNames"]["py"]),
        tuple(raw["movedClientMembers"]["py"]),
        raw["config"]["py"],
        tuple(raw["cloudApiUrls"]),
        tuple(package["old"]),
        package["new"],
        requirement,
        specifier,
        tuple(package["extras"]),
        tuple(raw["v0Modules"]["py"]["old"]),
        raw["docs"]["base"],
        receivers,
        suggestions,
        tuple(
            trap.get("short", trap["text"])
            for trap in raw["dataTraps"]
            if trap["id"] != "same-platform"
        ),
        tuple(exports)
        if isinstance(exports, list) and all(isinstance(name, str) for name in exports)
        else None,
        raw.get("v0Version"),
        {
            str(alias): str(category)
            for alias, category in raw.get("rootAliasCategories", {}).items()
        },
        tuple(package["knownExtras"]),
    )


@dataclass(frozen=True)
class SourceResult:
    source: str
    items: tuple[Item, ...]
    found: bool
    requirement_found: bool = False


@dataclass(frozen=True)
class TransformOptions:
    mapping: Mapping
    mode: Mode = "upgrade"
    drop_api_url: bool = False
