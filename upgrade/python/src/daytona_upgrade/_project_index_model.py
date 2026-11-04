# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""First-party import ownership and cross-file flow evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from .pytest_collection import PytestCollection
from .wildcard_exports import UnresolvedExports


@dataclass(frozen=True)
class SourceContext:
    """Source identity plus the optional filesystem boundary for read-only preflight."""

    file: str
    module: str
    index: ProjectIndex
    root: Path | None = None


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class ProjectIndex:
    exports: Mapping[str, dict[str, str]]
    config_escapes: frozenset[tuple[str, str]] = frozenset()
    config_hooks: bool = False
    builtin_writes: frozenset[str] = frozenset()
    wildcard_exports: dict[str, frozenset[str] | UnresolvedExports] = field(
        default_factory=dict
    )
    collection: PytestCollection = PytestCollection()
    skipped_sources: frozenset[tuple[str, str]] = frozenset()
    class_mutations: frozenset[str] = frozenset()
    config_readers: frozenset[tuple[str, str]] = frozenset()
    parameter_links: frozenset[tuple[str, int, str]] = frozenset()
    adaptable_factories: frozenset[str] = frozenset()
    namespace_factories: frozenset[str] = frozenset()
    namespace_calls: Mapping[tuple[str, int, int], frozenset[str]] = field(
        default_factory=dict
    )

    @cached_property
    def roots(self) -> frozenset[str]:
        """Ownership is fixed for the lifetime of this export-index snapshot."""
        return frozenset(name.split(".", 1)[0] for name in self.exports)

    def owns(self, module: str) -> bool:
        """A first-party import root owns even names absent from its exports."""
        root = module.split(".", 1)[0]
        return root in self.roots

    def wildcard_names(self, module: str) -> frozenset[str]:
        """Explicit __all__ membership overrides the default public-name rule."""
        exports = self.exports.get(module, {})
        declared = self.wildcard_exports.get(module)
        if isinstance(declared, UnresolvedExports):
            return declared.names
        return (
            frozenset(declared & exports.keys())
            if declared is not None
            else frozenset(name for name in exports if not name.startswith("_"))
        )
