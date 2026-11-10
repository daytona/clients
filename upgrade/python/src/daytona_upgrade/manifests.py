# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Formatting-preserving Python dependency manifest migration."""

from __future__ import annotations

import re
from configparser import Error as ConfigError
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from libcst import ParserSyntaxError
from packaging.requirements import InvalidRequirement, Requirement
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Array, InlineTable, String, Table
from tomlkit.toml_document import TOMLDocument

from ._dependencies import Migration
from ._manifest_cfg import setup_cfg
from ._manifest_resolvers import pdm_overrides, resolver_array
from ._manifest_setup import rewrite_setup, runtime_setup_requirements
from ._requirements import is_requirements, records
from .manifest_errors import ManifestParseError
from .models import Mapping
from .python_floor import PYTHON_REVIEW, needs_python_review


def _at(
    document: TOMLDocument | Table | InlineTable | OutOfOrderTableProxy, path: str
) -> Table | InlineTable | OutOfOrderTableProxy | None:
    current = document
    for name in path.split("."):
        child = current.get(name)
        if not isinstance(child, (Table, InlineTable, OutOfOrderTableProxy)):
            return None
        current = child
    return (
        current
        if isinstance(current, (Table, InlineTable, OutOfOrderTableProxy))
        else None
    )


def _toml(source: str, migration: Migration, pipfile: bool) -> str:
    document = tomlkit.parse(source)
    migration.newline = "\r\n" if "\r\n" in source else "\n"
    python_fields = (
        (("requires", "python_version"), ("requires", "python_full_version"))
        if pipfile
        else (("project", "requires-python"), ("tool.poetry.dependencies", "python"))
    )
    for path, key in python_fields:
        table = _at(document, path)
        value = table.get(key) if table is not None else None
        if isinstance(value, String) and needs_python_review(str(value)):
            migration.manual.append((0, PYTHON_REVIEW))
    if pipfile:
        for path in ("packages", "dev-packages"):
            migration.certify = path == "packages"
            table = _at(document, path)
            if table is not None:
                migration.dependencies(table)
        return tomlkit.dumps(document)
    sources = _at(document, "tool.uv.sources")
    overrides = (
        [
            name
            for name in sources
            if re.sub(r"[-_.]+", "-", str(name)).lower() in migration.names
        ]
        if sources is not None
        else []
    )
    if overrides:
        migration.found = True
        migration.manual.append(
            (
                0,
                f"Review tool.uv.sources overrides for {', '.join(str(name) for name in overrides)} and set the dependency to {migration.mapping.requirement} manually; source overrides and dependency declarations were preserved.",
            )
        )
        return source
    project = _at(document, "project")
    if project is not None:
        dependencies = project.get("dependencies")
        if isinstance(dependencies, Array):
            migration.array(dependencies)
    uv = _at(document, "tool.uv")
    migration.certify = False
    if uv is not None:
        for field in ("override-dependencies", "constraint-dependencies"):
            value = uv.get(field)
            if isinstance(value, Array):
                resolver_array(value, migration, f"tool.uv.{field}")
        dependencies = uv.get("dev-dependencies")
        if isinstance(dependencies, Array):
            migration.array(dependencies)
    pdm = _at(document, "tool.pdm.resolution.overrides")
    if pdm is not None:
        pdm_overrides(pdm, migration)
    for path in (
        "project.optional-dependencies",
        "dependency-groups",
        "tool.pdm.dev-dependencies",
    ):
        table = _at(document, path)
        if table is not None:
            for value in table.values():
                if isinstance(value, Array):
                    migration.array(value)
    dependencies = _at(document, "tool.poetry.dependencies")
    migration.certify = True
    if dependencies is not None:
        migration.dependencies(dependencies)
    poetry = _at(document, "tool.poetry")
    extras = _at(document, "tool.poetry.extras")
    if migration.renamed and poetry is not None and "extras" in poetry:
        if extras is None:
            migration.manual.append(
                (
                    0,
                    "Review Poetry extras references manually before renaming the Daytona dependency; this manifest was preserved.",
                )
            )
        if extras is None or not migration.references(extras):
            migration.changes.clear()
            migration.manual = [
                item for item in migration.manual if item not in migration.dropped
            ]
            migration.v2_found = False
            return source
    groups = _at(document, "tool.poetry.group")
    migration.certify = False
    if groups is not None:
        for group in groups.values():
            if isinstance(group, (Table, InlineTable, OutOfOrderTableProxy)):
                dependencies = _at(group, "dependencies")
                if dependencies is not None:
                    migration.dependencies(dependencies)
    legacy = _at(document, "tool.poetry.dev-dependencies")
    if legacy is not None:
        migration.dependencies(legacy)
    return tomlkit.dumps(document)


@dataclass(frozen=True)
class ManifestResult:
    """Dependency recognition is independent of automatic edits."""

    source: str
    changes: tuple[tuple[str, str], ...]
    manual: tuple[tuple[int, str], ...]
    found: bool
    v2_found: bool = False
    resolver_review: tuple[tuple[int, str], ...] = ()
    # Each Daytona declaration's package name and version constraint ("" for none).
    declared: tuple[tuple[str, str], ...] = ()


def inspect_manifest(path: Path, source: str, mapping: Mapping) -> ManifestResult:
    """Attach the manifest's logical filename to supported parser failures."""
    try:
        return _inspect_manifest(path, source, mapping)
    except (TOMLKitError, ConfigError, ParserSyntaxError, SyntaxError) as error:
        raise ManifestParseError(
            path,
            str(error),
            "TOML"
            if path.name in {"pyproject.toml", "Pipfile"}
            else "Python"
            if path.suffix == ".py"
            else "INI",
        ) from error


def _inspect_manifest(path: Path, source: str, mapping: Mapping) -> ManifestResult:
    """Return migrated text and actual dependency edits; never write a file."""
    migration = Migration(mapping)
    bom = "\ufeff" if source.startswith("\ufeff") else ""
    source = source.removeprefix(bom) if bom else source
    # Python 3.9 compatibility precludes structural pattern matching here.
    if path.name in ("pyproject.toml", "Pipfile"):
        result = _toml(source, migration, path.name == "Pipfile")
    elif path.name == "setup.cfg":
        result = setup_cfg(source, migration, path.parent)
    elif path.name == "setup.py":
        result = rewrite_setup(source, migration.requirement, migration.manual_setup)
        proofs: list[bool] = []
        for requirement in runtime_setup_requirements(source):
            runtime = Migration(mapping)
            runtime.requirement(requirement)
            migration.declared.extend(runtime.declared)
            if runtime.found:
                proofs.append(runtime.v2_found)
        migration.v2_found = bool(proofs) and all(proofs)
    elif is_requirements(path):
        rewritten: list[str] = []
        for number, record in records(source):
            migration.line = number
            rewritten.append(migration.requirement(record))
        result = "".join(rewritten)
    else:
        result = source
    return ManifestResult(
        bom + result,
        tuple(migration.changes),
        tuple(migration.manual),
        migration.found,
        migration.v2_found,
        tuple(migration.resolver_review),
        tuple(migration.declared),
    )


def rewrite_manifest(
    path: Path, source: str, mapping: Mapping
) -> tuple[str, list[tuple[str, str]]]:
    """Return migrated text and actual dependency edits; never write a file."""
    result = inspect_manifest(path, source, mapping)
    return result.source, list(result.changes)


def next_step(root: Path, requirement: str | None) -> str:
    """Select a lock command from the root lockfile, without running it."""
    for lockfile, command in (
        ("uv.lock", "uv lock"),
        ("poetry.lock", "poetry lock"),
        ("pdm.lock", "pdm lock"),
        ("Pipfile.lock", "pipenv lock"),
    ):
        if (root / lockfile).is_file():
            return command
    if requirement is None or " = " in requirement or "#" in requirement:
        return "Install the updated requirements."
    try:
        parsed = Requirement(requirement)
    except InvalidRequirement:
        return "Install the updated requirements."
    if parsed.marker is not None or parsed.url:
        return "Install the updated requirements."
    extras = f"[{','.join(sorted(parsed.extras))}]" if parsed.extras else ""
    return f'pip install -U "{parsed.name}{extras}{parsed.specifier}"'
