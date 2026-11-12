# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Deterministic project discovery and complete in-memory migration preflight."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from . import __version__
from .dependency_scope import inspect_parent_dependencies
from .environment_python import scan_notebook_environment, scan_python_environment
from .manifest_errors import ManifestParseError
from .models import Item, TransformOptions
from .notebooks import Notebook, notebook_failure, read_notebook, transform_notebook
from .partial_analysis import partial_analysis
from .project_discovery import discover
from .project_discovery import module_name as module_name
from .project_environment import ProjectEnvironment
from .project_index import ProjectLayout
from .project_paths import (
    PYTHON_SOURCE_SUFFIXES,
    environment_base,
    is_manifest,
    shadowing_module,
)
from .project_progress import project_progress
from .project_result import Change as Change
from .project_result import Dependency as Dependency
from .project_result import ProjectResult as ProjectResult
from .pytest_collection import read_collection
from .report_items import unique_items
from .sdk_target import companion_command
from .v0_version import ancestor_guidance


def preflight(
    root: Path, options: TransformOptions, progress: Callable[[str], None] | None = None
) -> ProjectResult:
    """Calculate every edit before the caller is allowed to write anything."""
    from .flow import SourceContext, build_index
    from .manifests import inspect_manifest
    from .transform import transform_source

    items: list[Item] = []
    files = discover(root, items)
    report_progress = project_progress(len(files), progress)
    base = root.parent if root.is_file() else root
    environment_root = environment_base(base)
    compose_directories = tuple(
        sorted(
            {
                directory
                for file in files
                if file.compose
                for directory in (file.path.parent, *file.compose_directories)
            }
        )
    )
    environment = ProjectEnvironment(base, environment_root, compose_directories)
    encoding_items: list[Item] = []
    if options.mode == "upgrade":
        collision, local_items = shadowing_module(
            {
                file.path: file.source
                for file in files
                if not file.requirements
                and (
                    file.path.suffix in PYTHON_SOURCE_SUFFIXES
                    or file.path.name == "pyproject.toml"
                )
            },
            base,
            scan_tree=root.is_dir(),
        )
        if collision:
            return ProjectResult((), (), (), False, collision)
        items.extend(local_items)
    project_roots = {file.path.parent for file in files if is_manifest(file.path)}
    notebooks: dict[str, Notebook] = {}
    sources: dict[str, str] = {}
    for file in files:
        if file.requirements:
            continue
        if file.path.suffix == ".ipynb":
            try:
                notebook = read_notebook(file.source)
            except ValueError as error:
                items.append(notebook_failure(file.relative, error))
                continue
            notebooks[file.relative] = notebook
            sources[module_name(file.relative)] = notebook.combined
        elif file.path.suffix in PYTHON_SOURCE_SUFFIXES:
            sources[module_name(file.relative)] = file.source
    layout = ProjectLayout(
        {
            module_name(file.relative): file.relative
            for file in files
            if not file.requirements
            and (
                file.path.suffix in PYTHON_SOURCE_SUFFIXES
                or file.path.suffix == ".ipynb"
            )
        },
        read_collection(base),
        report_progress,
    )
    index = build_index(sources, options.mapping, layout)
    changes: list[Change] = []
    dependencies: list[Dependency] = []
    found = False
    dependency_roots: set[Path] = set()
    rewritten_sources: dict[Path, str] = {}
    declared: list[tuple[Path, str, str]] = []
    for number, file in enumerate(files, 1):
        if report_progress:
            report_progress.advance(number, file.relative)
        if (
            not file.requirements
            and file.path.suffix == ".ipynb"
            and file.relative not in notebooks
        ):
            continue
        owner = next(
            (parent for parent in file.path.parents if parent in project_roots), base
        )
        output = file.source
        context = SourceContext(file.relative, module_name(file.relative), index, base)
        if not file.requirements and (
            file.path.suffix in PYTHON_SOURCE_SUFFIXES or file.path.suffix == ".ipynb"
        ):
            if file.relative in notebooks:
                try:
                    items.extend(
                        scan_notebook_environment(
                            notebooks[file.relative], options, context
                        )
                    )
                    result = transform_notebook(
                        notebooks[file.relative], options, context
                    )
                except ValueError as error:
                    items.append(notebook_failure(file.relative, error))
                    continue
            else:
                items.extend(scan_python_environment(file.source, options, context))
                result = transform_source(file.source, options, context)
            if file.encoding_error is not None and any(
                item["kind"] == "unsupported-file" for item in result.items
            ):
                encoding_items.append(file.encoding_error.item(base))
                result = replace(result, items=())
            output = result.source
            if (
                output != file.source
                and options.mode == "upgrade"
                and Change(file, output).changes_runtime()
            ):
                rewritten_sources.setdefault(owner, file.relative)
            items.extend(result.items)
            if any(item["kind"] == "unsupported-file" for item in result.items):
                continue  # setup.py is also a manifest; report its parse failure once.
            found = found or result.found or "daytona-v2:" in file.source
            if result.requirement_found:
                dependency_roots.add(owner)
        if is_manifest(file.path) or file.requirements:
            try:
                manifest = inspect_manifest(
                    Path("requirements.txt") if file.requirements else file.path,
                    output,
                    replace(options.mapping, extras=())
                    if file.constraint
                    else options.mapping,
                )
            except ManifestParseError as error:
                items.append(
                    Item(
                        file=file.relative,
                        line=0,
                        column=0,
                        kind="unsupported-file",
                        message=str(error).replace(
                            error.path.as_posix(), file.relative, 1
                        ),
                    )
                )
                continue
            found = found or manifest.found
            if not file.constraint:  # Constraints limit versions; they install nothing.
                declared.extend(
                    (file.path, name, specifier)
                    for name, specifier in manifest.declared
                )
            if manifest.found:
                dependency_roots.add(owner)
            if file.constraint and manifest.found and not manifest.manual:
                items.append(
                    Item(
                        file=file.relative,
                        line=0,
                        column=0,
                        kind="manual-dependency",
                        message=f"Constraints cannot enable extras. Set the consuming requirement to {options.mapping.requirement}; its constraint version was migrated where safe.",
                    )
                )
            items.extend(
                Item(
                    file=file.relative,
                    line=line,
                    column=1 if line else 0,
                    kind="manual-dependency",
                    message=message,
                )
                for line, message in manifest.manual
            )
            if options.mode == "upgrade":
                output = manifest.source
                dependencies.extend(
                    Dependency(file.relative, before, after)
                    for before, after in manifest.changes
                )
        items.extend(environment.scan(file, options))
        if file.path.name == "package.json":
            items.append(
                Item(
                    file=file.relative,
                    line=0,
                    column=0,
                    kind="other-ecosystem",
                    message=f"Run {companion_command(__version__)} for TypeScript / JavaScript code.",
                )
            )
        if output != file.source:
            if file.encoding_error is not None:
                encoding_items.append(file.encoding_error.item(base))
            else:
                changes.append(Change(file, output))
    # Finish scanning readable inputs for custom URLs before blocking all writes.
    if any(item["kind"] == "unsupported-file" for item in items):
        return ProjectResult(
            (), (), tuple([*encoding_items, *items]), found, declared=tuple(declared)
        )
    inspected_paths = {file.path.resolve() for file in files}
    owners = (
        rewritten_sources.keys() | dependency_roots
        if options.mode == "upgrade"
        else set()
    )
    for owner in sorted(owners):
        parent_proof, parent_items = inspect_parent_dependencies(
            owner, options.mapping, base
        )
        items.extend(
            item
            for item in parent_items
            if (base / item["file"]).resolve() not in inspected_paths
            and item not in items
        )
        if owner in dependency_roots or parent_proof:
            continue
        items.append(
            Item(
                file=rewritten_sources[owner],
                line=0,
                column=0,
                kind="manual-dependency",
                message=ancestor_guidance(owner, options.mapping),
            )
        )
    if encoding_items:
        return ProjectResult(
            (), (), tuple([*encoding_items, *items]), found, declared=tuple(declared)
        )
    if found and options.mode != "remove-suggestions":
        items.extend(partial_analysis(root))
    return ProjectResult(
        tuple(changes),
        tuple(dependencies),
        tuple(unique_items(items)),
        found,
        declared=tuple(declared),
    )
