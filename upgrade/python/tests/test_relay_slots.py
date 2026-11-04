# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conflicting tuple-return aliases cannot certify a single caller slot."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize("slot", [0, 1])
@pytest.mark.parametrize("heterogeneous", [False, True])
def test_reviews_fake_when_alias_returns_in_different_slots(
    tmp_path: Path, slot: int, heterogeneous: bool
) -> None:
    # Given both branches returning the injected client in different tuple slots.
    annotation = (
        "tuple[Daytona, Daytona, bool]" if heterogeneous else "tuple[Daytona, Daytona]"
    )
    first_flag = ", True" if heterogeneous else ""
    second_flag = ", False" if heterogeneous else ""
    sources = {
        "provider": "from __future__ import annotations\nfrom daytona import Daytona\n"
        f"def identity(c: Daytona, flag) -> {annotation}:\n"
        f"    alias = c\n    if flag: return alias, Daytona(){first_flag}\n"
        f"    return Daytona(), alias{second_flag}\n"
        "def consume(c: Daytona, flag):\n"
        f"    return identity(c, flag)[{slot}].create()\n",
        "app": "from types import SimpleNamespace\nfrom provider import consume\n"
        "def test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        f"    return consume(fake, {slot == 0})\n",
    }
    # When analyzing downstream moved access through either possible tuple slot.
    results = migrate_project(sources, tmp_path)
    # Then ambiguity is review-only, not a guessed safe return identity.
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
    assert ast.dump(ast.parse(results["app"].source)) == ast.dump(
        ast.parse(sources["app"])
    )
