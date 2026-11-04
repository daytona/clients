# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared normalized-key safety for mock configuration analysis and rewriting."""

from __future__ import annotations

from collections.abc import Collection, Iterable


def configuration_collision(names: Iterable[str], moved: Collection[str]) -> bool:
    """Detect duplicate destination paths across all arguments of one call."""
    seen: set[str] = set()
    moved_key = False
    namespace_key = False
    for name in names:
        moves = name.split(".", 1)[0] in moved
        moved_key |= moves
        namespace_key |= name == "v0" or name.startswith("v0.")
        if moved_key and namespace_key:
            return True
        normalized = "v0." + name if moves else name
        if normalized in seen:
            return True
        seen.add(normalized)
    return False
