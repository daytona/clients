# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative import-component filtering before expensive receiver analysis."""

from __future__ import annotations

import ast
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

from .mock_filter import unresolved_mock_targets
from .models import Mapping
from .resolution import relative_module
from .source_suffixes import PACKAGE_INITIALIZERS
from .wildcard_exports import UnresolvedExports, declared_exports

if TYPE_CHECKING:
    from .project_index import ProjectLayout

_SPECIAL: Final = re.compile(
    r"\benviron\b|\bputenv\b|dotenv|pydantic|builtins|importlib|__import__|import_module",
    re.I,
)


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class AnalysisPlan:
    """Parsed input and the connected modules whose flow can affect migration."""

    syntax: dict[str, ast.Module]
    modules: frozenset[str]


def plan_analysis(
    sources: dict[str, str], mapping: Mapping, layout: ProjectLayout
) -> AnalysisPlan:
    """Keep SDK, environment, mock/dynamic and builtin effects plus import neighbors.

    Both import directions are retained: unrelated providers can disprove a
    client, and consumers can expose or mutate a config. Ownership keys for
    omitted modules are still retained in the full project index.
    """
    markers = tuple(name.casefold() for name in (*mapping.modules, "daytona"))
    syntax: dict[str, ast.Module] = {}
    edges: dict[str, set[str]] = {module: set() for module in sources}
    roots: dict[str, set[str]] = {}
    for module in sources:
        roots.setdefault(module.split(".", 1)[0], set()).add(module)
    seeds: set[str] = set()
    for number, (module, source) in enumerate(sources.items(), 1):
        if layout.progress and (
            number == 1 or number % 50 == 0 or number == len(sources)
        ):
            layout.progress(
                f"Inspecting imports {number}/{len(sources)}: {layout.files.get(module, module)}"
            )
        normalized = unicodedata.normalize("NFKC", source).casefold()
        if any(marker in normalized for marker in markers) or _SPECIAL.search(
            normalized
        ):
            seeds.add(module)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # The transform still parses every input for diagnostics.
        syntax[module] = tree
        if re.search("patch|mock", normalized) and unresolved_mock_targets(tree):
            seeds.add(module)
        # Computed membership can turn an ordinary unknown binding into
        # review evidence, even when Daytona appears only in a manifest.
        if "__all__" in normalized and isinstance(
            declared_exports(source), UnresolvedExports
        ):
            seeds.add(module)
        targets: set[str] = set()
        current = module
        if layout.files.get(module, "").endswith(
            tuple("/" + name for name in PACKAGE_INITIALIZERS)
        ):
            current += ".__init__"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    # Unknown wildcard exports can introduce client evidence.
                    seeds.add(module)
                target = relative_module(current, node)
                targets.add(target)
                targets.update(target + "." + alias.name for alias in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    factory = (
                        decorator.func if isinstance(decorator, ast.Call) else decorator
                    )
                    if (
                        isinstance(factory, ast.Name)
                        and factory.id == "fixture"
                        or isinstance(factory, ast.Attribute)
                        and factory.attr == "fixture"
                    ):
                        seeds.add(module)
            elif isinstance(node, ast.Name) and node.id == "pytest_plugins":
                seeds.add(module)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = unicodedata.normalize("NFKC", node.value).casefold()
                if any(marker in value for marker in markers):
                    seeds.add(module)
                # Literal dynamic imports and mock targets also name modules.
                targets.add(node.value)
        for target in targets:
            for neighbor in roots.get(target.split(".", 1)[0], ()):
                edges[module].add(neighbor)
                edges[neighbor].add(module)
    # Ancestor conftests can supply fixtures to tests and decorated plugin
    # functions. Keep every descendant; flow decides injection eligibility.
    conftests = {
        module: PurePosixPath(file).parent
        for module in sources
        if PurePosixPath(
            file := layout.files.get(module, module.replace(".", "/") + ".py")
        ).name
        == "conftest.py"
    }
    # Pytest providers need not be imported or collected. Retain them even
    # outside a linked module's directory; flow determines fixture eligibility.
    seeds.update(conftests)
    for module in sources:
        file = layout.files.get(module, module.replace(".", "/") + ".py")
        path = PurePosixPath(file)
        for fixture, directory in conftests.items():
            if directory in path.parents:
                edges[module].add(fixture)
                edges[fixture].add(module)
    selected: set[str] = set()
    pending = list(seeds)
    while pending:
        module = pending.pop()
        if module not in selected:
            selected.add(module)
            pending.extend(edges[module] - selected)
    return AnalysisPlan(syntax, frozenset(selected))
