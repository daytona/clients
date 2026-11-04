# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective aliases must deny sole-import certification, never grant it."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "mutation",
    [
        "run = exec; run(code)",
        "run = eval; run(code)",
        'g = globals; g()["C"] = other',
        'g = locals; g()["C"] = other',
        'g = vars; g()["C"] = other',
        'from builtins import vars as v; v()["C"] = other',
        'run = getattr(builtins, "exec"); run(code)',
        'if flag: g = vars\ng()["C"] = other',
        'g = vars if flag else other; g()["C"] = other',
        'g: Callable = vars; g()["C"] = other',
        'g, other = vars, None; g()["C"] = other',
        'g = globals; alias = g; alias()["C"] = other',
        'getattr(builtins, "exec")(code)',
    ],
)
def test_reviews_import_when_reflective_alias_can_rebind_it(mutation: str) -> None:
    # Given a possibly rewritten SDK import behind an ImportError guard.
    source = (
        "import builtins\ntry:\n    from daytona import Daytona as C\n"
        f"except ImportError:\n    pass\n{mutation}\n"
        "def make() -> C: return unknown()\nvalue = make()\nvalue.create()\n"
    )
    # When deriving sole-writer certification.
    result = migrate(source)
    # Then no alias conceals the namespace write from the proof fence.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)
