# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The helpers tests use to read files outside sdk-python/ (``tests/_repo.py``)."""

from __future__ import annotations

from pathlib import Path

import _repo
import pytest


def test_in_a_checkout_they_return_the_path_and_a_missing_file_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_repo, "IN_CHECKOUT", True)
    assert _repo._repo_file("contract/api-openapi.json") == _repo.REPO_ROOT / "contract" / "api-openapi.json"
    assert _repo._repo_scripts() == _repo.REPO_ROOT / "scripts"
    with pytest.raises(FileNotFoundError):
        _repo._repo_file("contract/no-such-file.json").read_text()


def test_outside_a_checkout_they_skip_naming_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_repo, "IN_CHECKOUT", False)
    with pytest.raises(pytest.skip.Exception, match="contract/api-openapi.json"):
        _repo._repo_file("contract/api-openapi.json")
    with pytest.raises(pytest.skip.Exception, match="scripts/"):
        _repo._repo_scripts()


def test_an_unrelated_missing_file_is_never_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the helpers skip: a file the SDK should have written still fails,
    # wherever the tree is unpacked.
    monkeypatch.setattr(_repo, "IN_CHECKOUT", False)
    with pytest.raises(FileNotFoundError):
        (tmp_path / "never-written.txt").read_text()


def test_this_tree_is_detected() -> None:
    # sdk-python/ next to contract/ in a checkout; an sdist unpacks as daytona-<version>/.
    assert _repo.IN_CHECKOUT == (_repo.SDK_ROOT.name == "sdk-python")
