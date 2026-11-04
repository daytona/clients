# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Observe export-summary reads at the shared project-index boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class ExportReads(Mapping[str, dict[str, str]]):
    """Record consulted modules, including absent summaries and local callees.

    Mapping's get/items/values delegate to __getitem__, so imports, namespace
    attributes, fixture injection and self-summary reads share this one seam.
    Iterating ownership keys alone does not depend on their summary values.
    The read set is intentionally mutable during one module's analysis.
    """

    exports: Mapping[str, dict[str, str]]
    modules: set[str] = field(default_factory=set)

    def __getitem__(self, module: str) -> dict[str, str]:
        self.modules.add(module)
        return self.exports[module]

    def __iter__(self) -> Iterator[str]:
        return iter(self.exports)

    def __len__(self) -> int:
        return len(self.exports)
