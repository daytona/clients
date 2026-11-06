# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Root-module evidence shared by factory summaries and uncertain value joins."""

from __future__ import annotations

from typing import Final

# Package directories in the bundled 0.x Python SDK (not root __all__).
V0_SUBMODULES: Final = frozenset(
    {"_async", "_sync", "_utils", "common", "handle", "internal"}
)
ROOT_MODULES: Final = frozenset({"module:daytona", "module:daytona_sdk"})
REVIEW_MODULE: Final = "unproven:module:daytona"
REVIEW_MODULE_CLIENT: Final = "unproven:module:daytona+client"


def module_review(kind: str) -> str:
    """Preserve root-module identity without promoting a possible value to proof."""
    if kind in ROOT_MODULES or kind == REVIEW_MODULE:
        return REVIEW_MODULE
    if kind == REVIEW_MODULE_CLIENT:
        return kind
    if kind.startswith("fn:") and (review := module_review(kind[3:])):
        return "fn:" + review
    return ""
