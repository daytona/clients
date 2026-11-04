# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Project lookup interceptors fence SDK members throughout the hierarchy."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize("hook", ["__getattribute__", "__getattr__"])
@pytest.mark.parametrize("placement", ["direct", "inherited", "before", "after"])
@pytest.mark.parametrize(
    "access", ["Custom().create()", "getattr(Custom(), 'create')()"]
)
def test_member_is_manual_when_any_project_layer_intercepts_lookup(
    hook: str, placement: str, access: str
) -> None:
    # Given a project hook, including a mixin after the SDK in C3 order.
    body = f"    def {hook}(self, name): return replacement\n"
    declarations = {
        "direct": "class Custom(Daytona):\n" + body,
        "inherited": "class Parent(Daytona):\n" + body + "class Custom(Parent): pass\n",
        "before": "class Mixin:\n" + body + "class Custom(Mixin, Daytona): pass\n",
        "after": "class Mixin:\n" + body + "class Custom(Daytona, Mixin): pass\n",
    }
    source = "from daytona import Daytona\n" + declarations[placement] + access + "\n"
    # When migrating an inherited-looking member or literal builtin lookup.
    result = migrate(source)
    # Then the intercepted expression is preserved with a manual item.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
