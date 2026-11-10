# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Project path classification, independent of the selected scan root."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Array, InlineTable, String, Table

from ._requirements import SKIP, is_requirements
from .manifests import _at
from .models import Item
from .safety import _REPOSITORY_ENV

# Python sources the tool reads and rewrites: modules, stubs and windowed
# scripts (`.pyw`, which Windows also imports as modules).
PYTHON_SOURCE_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})


def environment_base(base: Path) -> Path:
    """Classify deployment paths relative to the checkout.

    Without a Git answer the whole absolute path is classified: narrowing the scan root
    must never hide a deployment file, and scanning one extra YAML file is the
    safe failure.
    """
    anchor = Path(base.resolve().anchor)
    if not any((directory / ".git").exists() for directory in (base, *base.parents)):
        return anchor
    env = {
        key: value for key, value in os.environ.items() if key not in _REPOSITORY_ENV
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            env={**env, "LC_ALL": "C", "LANG": "C"},
            check=False,
        )
    except OSError:
        return anchor
    return (
        Path(result.stdout.strip()).resolve()
        if result.returncode == 0 and result.stdout.strip()
        else anchor
    )


def is_environment(path: Path) -> bool:
    name = path.name
    return (
        name.startswith((".env", "Dockerfile"))
        or (
            name.startswith(("docker-compose", "compose"))
            and path.suffix in {".yaml", ".yml"}
        )
        or (
            path.suffix in {".yaml", ".yml"}
            and bool(set(path.parts) & {"k8s", "helm", "charts", "deploy"})
        )
        or (".github/workflows/" in path.as_posix())
    )


def is_manifest(path: Path) -> bool:
    return path.name in {
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "Pipfile",
    } or is_requirements(path)


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class LocalModule:
    """Project-relative first-party source owning an SDK import root."""

    name: str
    path: Path

    def message(self) -> str:
        return (
            f"This project has its own top-level `{self.name}` module ({self.path.as_posix()}), "
            "which conflicts with the Daytona SDK. Rename it, then rerun."
        )


def shadowing_module(
    sources: Mapping[Path, str], base: Path, *, scan_tree: bool = True
) -> tuple[LocalModule | None, tuple[Item, ...]]:
    """Refuse import-root collisions and report conditional nested shadowing."""
    roots = {base: "", base / "src": ""}
    candidates = set(sources)
    scan_roots = (base,) if scan_tree else (base / "daytona", base / "daytona_sdk")
    for directory, dirs, files in (
        entry for root in scan_roots if not root.is_symlink() for entry in os.walk(root)
    ):
        parent = Path(directory)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in SKIP and not (parent / name).is_symlink()
        )
        if any(
            is_manifest(parent / name) and not (parent / name).is_symlink()
            for name in files
        ):
            roots[parent] = ""
        manifest = parent / "pyproject.toml"
        if manifest in sources:
            roots.update(_import_roots(manifest, sources[manifest]))
        candidates.update(
            parent / name
            for name in files
            if Path(name).suffix in PYTHON_SOURCE_SUFFIXES
        )
    if not scan_tree:
        # A single file runs with its directory first on sys.path, so a sibling
        # module or package of the SDK's name shadows the SDK.
        for name in ("daytona", "daytona_sdk"):
            candidates.update(
                (
                    base / name / "__init__.py",
                    *(base / f"{name}{suffix}" for suffix in PYTHON_SOURCE_SUFFIXES),
                )
            )
    items: list[Item] = []
    for path in sorted(candidates):
        relative = path.relative_to(base)
        if (
            set(relative.parts) & SKIP
            or not path.is_file()
            or any(parent.is_symlink() for parent in (path, *path.parents))
        ):
            continue
        for root, prefix in roots.items():
            if prefix in {"daytona", "daytona_sdk"} and path == root / "__init__.py":
                return LocalModule(prefix, root.relative_to(base)), ()
            if (
                not prefix
                and root in path.parents
                and path.suffix in PYTHON_SOURCE_SUFFIXES
            ):
                parts = path.relative_to(root).parts
                if len(parts) > 1 and parts[0] in {"daytona", "daytona_sdk"}:
                    return LocalModule(
                        parts[0], (root / parts[0]).relative_to(base)
                    ), ()
        location = path.parent if path.name == "__init__.py" else path
        name = location.name if location != path else path.stem
        if (
            name not in {"daytona", "daytona_sdk"}
            or path.suffix not in PYTHON_SOURCE_SUFFIXES
        ):
            continue
        if roots.get(location.parent) == "":
            return LocalModule(name, location.relative_to(base)), ()
        items.append(
            Item(
                file=relative.as_posix(),
                line=0,
                column=0,
                kind="private-import",
                message=f"Local `{name}` module ({location.relative_to(base).as_posix()}) would shadow the Daytona SDK only if its parent directory is on sys.path; review your import paths.",
            )
        )
    return None, tuple(items)


def _import_roots(manifest: Path, manifest_source: str) -> dict[Path, str]:
    """Parse discovered packaging roots without rereading files or executing code."""
    try:
        document = tomlkit.parse(manifest_source)
    except TOMLKitError:
        # Manifest inspection owns malformed TOML; default import roots still apply.
        return {}
    base = manifest.parent
    roots = {base: "", base / "src": ""}
    find = _at(document, "tool.setuptools.packages.find")
    where = find.get("where") if find is not None else None
    if isinstance(where, Array):
        roots.update(
            (base / str(value), "") for value in where if isinstance(value, String)
        )
    package_dir = _at(document, "tool.setuptools.package-dir")
    if package_dir is not None:
        roots.update(
            (base / str(value), str(name))
            for name, value in package_dir.items()
            if isinstance(value, String)
        )
    poetry = _at(document, "tool.poetry")
    packages = poetry.get("packages") if poetry is not None else None
    if isinstance(packages, Array):
        for package in packages:
            if isinstance(package, (Table, InlineTable)):
                source = package.get("from")
                if isinstance(source, String):
                    roots[base / str(source)] = ""
    for table_path in ("tool.hatch.build", "tool.hatch.build.targets.wheel"):
        hatch = _at(document, table_path)
        if hatch is None:
            continue
        sources = hatch.get("sources")
        if isinstance(sources, Array):
            roots.update(
                (base / str(value), "")
                for value in sources
                if isinstance(value, String)
            )
        elif isinstance(sources, (Table, InlineTable)):
            roots.update((base / str(value), "") for value in sources)
        packages = hatch.get("packages")
        if isinstance(packages, Array):
            roots.update(
                (base / str(value).rsplit("/", 1)[0], "")
                for value in packages
                if isinstance(value, String) and "/" in str(value)
            )
    return roots
