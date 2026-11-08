# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The helpers tests use to read files outside sdk-python/ (``tests/_repo.py``)."""

from __future__ import annotations

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


def test_the_helpers_name_each_file_they_skip_for(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each helper call decides on its own; a different file gets its own name,
    # and turning the checkout back on returns the path again.
    monkeypatch.setattr(_repo, "IN_CHECKOUT", False)
    for relative in ("upgrade/mapping/mapping.json", "sdk-typescript/src/v2/index.ts"):
        with pytest.raises(pytest.skip.Exception, match=relative):
            _repo._repo_file(relative)
    monkeypatch.setattr(_repo, "IN_CHECKOUT", True)
    assert (
        _repo._repo_file("upgrade/mapping/mapping.json")
        == _repo.REPO_ROOT / "upgrade" / "mapping" / "mapping.json"
    )


def test_this_tree_is_detected() -> None:
    # A checkout: the SDK in sdk-python/ next to contract/. An sdist unpacks
    # as daytona-<version>/ with neither.
    expected = _repo.SDK_ROOT.name == "sdk-python" and (_repo.REPO_ROOT / "contract").is_dir()
    assert _repo.IN_CHECKOUT == expected
