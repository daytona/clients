# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Files outside ``sdk-python/`` that drift and parity tests read.

A test that reads one (the contracts, the TypeScript SDK, ``scripts/``,
``upgrade/``) gets its path from :func:`_repo_file` or :func:`_repo_scripts`.
In a repository checkout they return it; from an sdist, which carries only the
SDK, they skip the calling test, naming the file. Nothing else is skipped: a
missing file anywhere else still fails its test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parent
# A repository checkout: the SDK sits in sdk-python/ next to contract/. An
# unpacked sdist (daytona-<version>/) has neither, wherever it is unpacked.
IN_CHECKOUT = SDK_ROOT.name == "sdk-python" and (REPO_ROOT / "contract").is_dir()


def _repo_file(relative_path: str) -> Path:
    """``relative_path`` in the repository (e.g. ``contract/api-openapi.json``).

    In a checkout it is returned as is, so a missing file still fails the
    test; outside one (an sdist) the calling test is skipped, naming the file.
    """
    if not IN_CHECKOUT:
        pytest.skip(f"repository file {relative_path} is not in this tree (an sdist)")
    return REPO_ROOT / relative_path


def _repo_scripts() -> Path:
    """The repository's ``scripts/``, which the release-script tests drive.

    Returned in a checkout; outside one (an sdist) the calling test is skipped.
    """
    if not IN_CHECKOUT:
        pytest.skip("the repository's scripts/ is not in this tree (an sdist)")
    return REPO_ROOT / "scripts"
