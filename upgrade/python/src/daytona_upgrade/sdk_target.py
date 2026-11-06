# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The SDK release this tool writes into a project's dependencies."""

from __future__ import annotations

from packaging.version import Version


def companion_command(tool_version: str) -> str:
    """The command that runs the npm tool of this release: both tools are
    published at the SDK's version, in npm's spelling (aN -> -alpha.N). An
    unpublished build (0.x) names no release."""
    version = Version(tool_version)
    if version.major == 0:
        return "npx @daytona/upgrade"
    spelled = ".".join(map(str, (*version.release, 0, 0)[:3]))
    if version.pre is not None:
        stage, number = version.pre
        spelled += f"-{ {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[stage] }.{number}"
    return f"npx @daytona/upgrade@{spelled}"


def target_specifier(tool_version: str, fallback: str) -> str:
    """A published tool has the version of the SDK release it is published
    with (upgrade-tool-ci checks its mapping against that tree's SDKs), so it
    targets that release: a
    pre-release pins it (pre-releases may break), a stable release takes it or
    any later release of the same major. An unpublished build (version 0.x,
    the placeholder in git) keeps the mapping's specifier."""
    version = Version(tool_version)
    if version.major == 0:
        return fallback
    if version.is_prerelease:
        return f"=={version}"
    return f">={version},<{version.major + 1}"
