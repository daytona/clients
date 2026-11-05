# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The SDK release this tool writes into a project's dependencies."""

from __future__ import annotations

from packaging.version import Version


def target_specifier(tool_version: str, fallback: str) -> str:
    """A published tool has the version of the SDK release it was built and
    tested with in the same publish run, so it targets exactly that release: a
    pre-release pins it (pre-releases may break), a stable release takes it or
    any later release of the same major. An unpublished build (version 0.x,
    the placeholder in git) keeps the mapping's specifier."""
    version = Version(tool_version)
    if version.major == 0:
        return fallback
    if version.is_prerelease:
        return f"=={version}"
    return f">={version},<{version.major + 1}"
