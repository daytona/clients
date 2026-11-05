# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compare the project's Daytona 0.x version with the release SDK 2.x runs as .v0."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import tomlkit
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from tomlkit.exceptions import TOMLKitError

# Closest first: the nearest directory with a lockfile owns the install.
_LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock")
_NAMES = frozenset({"daytona", "daytona-sdk"})


class Warning(TypedDict):  # noqa: A001 -- the report's field name.
    file: str
    message: str


@dataclass(frozen=True)
class V0VersionCheck:
    warnings: tuple[Warning, ...]
    refusal: str | None = None


def older_message(version: str, pinned: str) -> str:
    return f"Daytona SDK {version} is older than {pinned}, the 0.x release SDK 2.x runs as .v0. Calls moved to .v0 now run {pinned}; review the 0.x changes between {version} and {pinned}, then run your tests."


def newer_patch_message(version: str, pinned: str) -> str:
    return f"Daytona SDK {version} is newer than {pinned}, the 0.x release SDK 2.x runs as .v0. Calls moved to .v0 now run {pinned}; review the 0.x changes between {pinned} and {version}, then run your tests."


def allowed_newer_message(version: str, pinned: str) -> str:
    return f"Daytona SDK {version} is newer than {pinned}, the 0.x release SDK 2.x runs as .v0. 0.x APIs added after {pinned} are missing from .v0 and get no FIXME (--allow-newer-v0); review them by hand."


def unknown_message(pinned: str) -> str:
    return f"Cannot determine which Daytona SDK 0.x version this project uses; SDK 2.x runs {pinned} as .v0. Review the 0.x changes between your version and {pinned}, then run your tests."


def newer_refusal(version: str, file: str, pinned: str) -> str:
    return f"Daytona SDK {version} ({file}) is newer than {pinned}, the 0.x release this daytona-upgrade and SDK 2.x run as .v0: 0.x APIs added after {pinned} are missing from .v0 and would get no FIXME. Use a later daytona-upgrade release, or rerun with --allow-newer-v0 to migrate anyway. No files were written."


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _zero_x(value: str) -> Version | None:
    try:
        version = Version(value.lstrip("=").strip())
    except InvalidVersion:
        return None
    return version if version.major == 0 else None


def _locked(path: Path) -> dict[str, list[str]]:
    """The versions a lockfile resolves, per normalized Daytona package name."""
    text = path.read_text(encoding="utf-8").removeprefix("\ufeff")
    entries: list[tuple[str, object]] = []
    if path.name == "Pipfile.lock":
        lock = json.loads(text)
        sections = (
            [lock.get(name) for name in ("default", "develop")]
            if isinstance(lock, dict)
            else []
        )
        entries = [
            (str(name), entry.get("version"))
            for section in sections
            if isinstance(section, dict)
            for name, entry in section.items()
            if isinstance(entry, dict)
        ]
    else:
        # uv, Poetry and PDM: [[package]] tables with name and version.
        packages = tomlkit.parse(text).unwrap().get("package", [])
        if isinstance(packages, list):
            entries = [
                (str(entry.get("name", "")), entry.get("version"))
                for entry in packages
                if isinstance(entry, dict)
            ]
    found: dict[str, list[str]] = {}
    for name, version in entries:
        if _normalized(name) in _NAMES and isinstance(version, str):
            found.setdefault(_normalized(name), []).append(version)
    return found


def _owner_lockfiles(directory: Path) -> list[Path]:
    current = directory.resolve()
    while True:
        found = [current / name for name in _LOCKFILES if (current / name).is_file()]
        if found or (current / ".git").exists() or current.parent == current:
            return found
        current = current.parent


def _lower_bound(specifier: str) -> Version | None:
    try:
        specifiers = SpecifierSet(specifier)
    except InvalidSpecifier:
        return None
    bounds: list[Version] = []
    for entry in specifiers:
        if entry.operator not in {"==", "===", ">=", "~=", ">"}:
            continue
        try:
            bounds.append(Version(entry.version.removesuffix(".*")))
        except InvalidVersion:
            continue
    return max(bounds) if bounds else None


def _poetry_lower_bound(specifier: str) -> Version | None:
    # Poetry caret/tilde and bare versions, as dependency_versions.requires_v2 reads them.
    normalized = re.sub(r"[\^~](?!=)\s*(\d+(?:\.\d+)*)", r">=\1", specifier.strip())
    if re.fullmatch(r"\d+(?:\.[\d*]+)*", normalized):
        normalized = "==" + normalized
    return _lower_bound(normalized)


def check_v0_version(
    base: Path,
    declared: tuple[tuple[Path, str, str], ...],
    pinned: str | None,
    allow_newer: bool,
) -> V0VersionCheck:
    """The owning lockfile's resolution of the declared package says what is
    installed; without one, the constraint's lower bound stands in. Older and
    newer-patch versions warn; a newer minor refuses unless ``allow_newer``,
    because its added APIs are in neither ``.v0`` nor the mapping."""
    if pinned is None:
        return V0VersionCheck(())
    pin = Version(pinned)
    observed: dict[tuple[Path, Version], None] = {}
    unknown: dict[Path, None] = {}
    owners: dict[Path, list[Path]] = {}
    locks: dict[Path, dict[str, list[str]]] = {}
    for manifest, name, specifier in declared:
        directory = manifest.parent
        lockfiles = owners.setdefault(directory, _owner_lockfiles(directory))
        resolved: list[tuple[Path, str]] = []
        for lockfile in lockfiles:
            if lockfile not in locks:
                try:
                    locks[lockfile] = _locked(lockfile)
                except (OSError, UnicodeError, ValueError, TOMLKitError):
                    locks[lockfile] = {}  # Unreadable or malformed: no evidence.
            resolved.extend(
                (lockfile, value)
                for value in locks[lockfile].get(_normalized(name), [])
            )
        if resolved:
            # A resolution outside 0.x (an already upgraded project) needs no check.
            for lockfile, value in resolved:
                version = _zero_x(value)
                if version is not None:
                    observed[(lockfile, version)] = None
            continue
        # No lockfile, or one that does not resolve this package: the constraint stands in.
        bound = _poetry_lower_bound(specifier) if specifier else None
        if bound is not None and bound.major >= 2:
            continue
        version = (
            None if bound is None or bound == Version("0") else _zero_x(str(bound))
        )
        if version is None:
            unknown[manifest] = None
        else:
            observed[(manifest, version)] = None

    def relative(path: Path) -> str:
        return Path(os.path.relpath(path, base)).as_posix()

    warnings: list[Warning] = []
    for path, version in sorted(observed, key=lambda key: (relative(key[0]), key[1])):
        shown, file = _text(version), relative(path)
        if version == pin:
            continue
        if version < pin:
            warnings.append(Warning(file=file, message=older_message(shown, pinned)))
        elif version.release[:2] > pin.release[:2]:
            if not allow_newer:
                return V0VersionCheck(
                    tuple(warnings), newer_refusal(shown, file, pinned)
                )
            warnings.append(
                Warning(file=file, message=allowed_newer_message(shown, pinned))
            )
        else:
            warnings.append(
                Warning(file=file, message=newer_patch_message(shown, pinned))
            )
    for path in sorted(unknown, key=relative):
        warnings.append(Warning(file=relative(path), message=unknown_message(pinned)))
    return V0VersionCheck(tuple(warnings))


def _text(version: Version) -> str:
    # Three components, as the TypeScript tool prints (0.224 -> 0.224.0).
    release = (
        (*version.release, 0, 0)[:3] if len(version.release) < 3 else version.release
    )
    rest = str(version)[len(".".join(map(str, version.release))) :]
    return ".".join(map(str, release)) + rest
