# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Compare the project's Daytona 0.x version with the release SDK 2.x runs as .v0."""

from __future__ import annotations

import functools
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import tomlkit
from packaging.version import InvalidVersion, Version
from tomlkit.exceptions import TOMLKitError

from .dependency_versions import lower_bound
from .models import Mapping

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
    # A local or development build (an editable checkout) names no release.
    if (
        version.is_devrelease
        or version.local is not None
        or version.release == (0, 0, 0)
    ):
        return None
    return version if version.major == 0 else None


@functools.cache
def _toml_packages(path: Path) -> tuple[dict[str, object], ...]:
    """A uv, Poetry or PDM lockfile's [[package]] tables, parsed once (tomllib
    where the interpreter has it: much faster than tomlkit on large locks)."""
    text = path.read_text(encoding="utf-8").removeprefix("\ufeff")
    if sys.version_info >= (3, 11):
        import tomllib

        packages: object = tomllib.loads(text).get("package", [])
    else:
        packages = tomlkit.parse(text).unwrap().get("package", [])
    if not isinstance(packages, list):
        return ()
    return tuple(entry for entry in packages if isinstance(entry, dict))


def _locked(path: Path) -> dict[str, list[str]]:
    """The versions a lockfile resolves, per normalized Daytona package name."""
    entries: list[tuple[str, object]] = []
    if path.name == "Pipfile.lock":
        lock = json.loads(path.read_text(encoding="utf-8").removeprefix("\ufeff"))
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
        entries = [
            (str(entry.get("name", "")), entry.get("version"))
            for entry in _toml_packages(path)
        ]
    found: dict[str, list[str]] = {}
    for name, version in entries:
        if _normalized(name) in _NAMES and isinstance(version, str):
            found.setdefault(_normalized(name), []).append(version)
    # Several versions of one package (forked resolutions, or projects the lock
    # does not tie to this manifest) do not say which one this project gets.
    return {
        name: versions for name, versions in found.items() if len(set(versions)) == 1
    }


def _uv_members(lockfile: Path) -> frozenset[str]:
    """The editable or virtual source paths a uv.lock records."""
    try:
        packages = _toml_packages(lockfile)
    except (OSError, UnicodeError, ValueError, TOMLKitError, RecursionError):
        return frozenset()
    return frozenset(
        str(path)
        for entry in packages
        if isinstance(source := entry.get("source"), dict)
        for path in (source.get("editable"), source.get("virtual"))
        if isinstance(path, str)
    )


def _owns(lockfile: Path, manifest: Path) -> bool:
    """A lockfile resolves the project file beside it, and a uv workspace
    member (an editable or virtual source) below it; other manifests below it
    are other projects."""
    if manifest.parent.resolve() == lockfile.parent.resolve():
        return True
    if lockfile.name != "uv.lock" or manifest.name != "pyproject.toml":
        return False
    member = Path(
        os.path.relpath(manifest.parent.resolve(), lockfile.parent.resolve())
    ).as_posix()
    return member in _uv_members(lockfile.resolve())


def _owner_lockfiles(directory: Path) -> list[Path]:
    current = directory.resolve()
    while True:
        found = [current / name for name in _LOCKFILES if (current / name).is_file()]
        if found or (current / ".git").exists() or current.parent == current:
            return found
        current = current.parent


_ANCESTOR_MANIFESTS = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "Pipfile",
    "requirements.txt",
    "requirements.in",
)


def ancestor_declarations(
    base: Path, mapping: Mapping
) -> tuple[tuple[Path, str, str], ...]:
    """The nearest ancestor manifest's Daytona declarations, up to the Git
    root, for a scan root that declares none itself (a partial-root run). A
    file root starts at its own directory, even outside a Git checkout."""
    from .manifest_errors import ManifestParseError
    from .manifests import inspect_manifest

    start = base.resolve()
    boundary = next(
        (
            directory
            for directory in (start, *start.parents)
            if (directory / ".git").exists()
        ),
        start.parent if start.is_file() else start,
    )
    if boundary == start:
        return ()
    for directory in start.parents:
        found: list[tuple[Path, str, str]] = []
        for name in _ANCESTOR_MANIFESTS:
            path = directory / name
            if not path.is_file():
                continue
            try:
                manifest = inspect_manifest(
                    Path("requirements.txt")
                    if name.startswith("requirements")
                    else path,
                    path.read_text(encoding="utf-8"),
                    mapping,
                )
            except (OSError, UnicodeError, ManifestParseError):
                continue
            found.extend((path, package, spec) for package, spec in manifest.declared)
        if found or directory == boundary:
            return tuple(found)
    return ()


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
    _toml_packages.cache_clear()  # Parsed once per run, never reused across runs.
    if not declared:
        # Daytona code with no declaration anywhere up to the Git root.
        return V0VersionCheck((Warning(file=".", message=unknown_message(pinned)),))
    pin = Version(pinned)
    evidenced: set[Path] = set()
    observed: dict[tuple[Path, Version], None] = {}
    unknown: dict[Path, None] = {}
    owners: dict[Path, list[Path]] = {}
    locks: dict[Path, dict[str, list[str]]] = {}
    for manifest, name, specifier in declared:
        # A declaration already on 2.x needs no check, even before its lockfile is refreshed.
        bound = lower_bound(specifier) if specifier else None
        if bound is not None and bound.major >= 2:
            evidenced.add(manifest)
            continue
        directory = manifest.parent
        lockfiles = [
            lockfile
            for lockfile in owners.setdefault(directory, _owner_lockfiles(directory))
            if _owns(lockfile, manifest)
        ]
        resolved: list[tuple[Path, str]] = []
        for lockfile in lockfiles:
            if lockfile not in locks:
                try:
                    locks[lockfile] = _locked(lockfile)
                except (
                    OSError,
                    UnicodeError,
                    ValueError,
                    TOMLKitError,
                    RecursionError,
                ):
                    locks[lockfile] = {}  # Unreadable or malformed: no evidence.
            resolved.extend(
                (lockfile, value)
                for value in locks[lockfile].get(_normalized(name), [])
            )
        if resolved:
            # A 2.x resolution (an already upgraded project) needs no check; a
            # local or development build names no release.
            local = False
            for lockfile, value in resolved:
                version = _zero_x(value)
                if version is not None:
                    observed[(lockfile, version)] = None
                else:
                    # A local build, or the 1.x shim release: no 0.x version to compare.
                    try:
                        local = local or Version(value.lstrip("=").strip()).major < 2
                    except InvalidVersion:
                        local = True
            if local:
                unknown[manifest] = None
            else:
                evidenced.add(manifest)
            continue
        # No lockfile, or one that does not resolve this package: the constraint stands in.
        version = (
            None if bound is None or bound == Version("0") else _zero_x(str(bound))
        )
        if version is None:
            unknown[manifest] = None
        else:
            observed[(manifest, version)] = None
            evidenced.add(manifest)

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
    # A manifest with another declaration that names a version is not unknown.
    for path in sorted(unknown.keys() - evidenced, key=relative):
        warnings.append(Warning(file=relative(path), message=unknown_message(pinned)))
    return V0VersionCheck(tuple(warnings))


def _text(version: Version) -> str:
    """The version as the TypeScript tool prints it (semver): three release
    components and a ``-alpha.N`` / ``-beta.N`` / ``-rc.N`` pre-release."""
    text = ".".join(map(str, (*version.release, 0, 0)[:3]))
    if version.pre is not None:
        stage, number = version.pre
        text += f"-{ {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[stage] }.{number}"
    if version.post is not None:
        text += f".post{version.post}"
    return text


def ancestor_guidance(owner: Path, mapping: Mapping) -> str:
    """The manual item for code whose project declares no SDK 2.x dependency,
    with the same decisions as a rewrite in the project root: a later major is
    never downgraded, a pre-release range above the target keeps its floor."""
    from .dependency_versions import (
        above_target_major,
        pinned_floor,
    )

    for _, name, specifier in ancestor_declarations(owner, mapping):
        if specifier and above_target_major(specifier, mapping.specifier):
            return f"{name}{specifier} is newer than this daytona-upgrade release targets ({mapping.specifier}); use the daytona-upgrade release of that SDK version."
    requirement = mapping.requirement
    for _, _name, specifier in ancestor_declarations(owner, mapping):
        floor = pinned_floor(specifier, mapping.specifier) if specifier else None
        if floor is not None:
            extras = f"[{','.join(mapping.extras)}]" if mapping.extras else ""
            requirement = f"{mapping.package_new}{extras}{floor}"
    return f"no Daytona dependency was updated; verify an unconditional runtime {requirement} requirement where the project declares it"
