# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Non-null client alternatives preserve rewrites without certifying None."""

from __future__ import annotations

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize(
    "expression",
    [
        "Daytona() and Daytona()",
        "Daytona() and Daytona() and Daytona()",
        "Daytona() if primary else Daytona()",
        "(Daytona() if primary else Daytona()) and Daytona()",
        "left and right",
        "left if primary else right",
    ],
)
def test_routes_join_when_every_possible_result_is_nonnull_client(
    expression: str,
) -> None:
    # Given concrete constructors, including aliases of their non-null results.
    source = (
        "from daytona import Daytona\nleft = Daytona()\nright = Daytona()\n"
        f"client = {expression}\nclient.create()\n"
    )
    # When joining the possible returned operands rather than their truth values.
    result = migrate(source)
    # Then the migrated operation is certain and needs no review.
    assert "client.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize(
    "expression",
    [
        "client and Daytona()",
        "client if primary else Daytona()",
        "Daytona() if primary else client",
        "alias and Daytona()",
        "alias if primary else Daytona()",
    ],
)
def test_reviews_join_when_an_operand_can_be_none(expression: str) -> None:
    # Given a nullable injected client whose annotation alone is not non-null proof.
    source = (
        "from typing import Optional\nfrom daytona import Daytona\n"
        "def run(client: Optional[Daytona], primary):\n"
        "    alias = client\n"
        f"    result = {expression}\n    return result.create()\n"
    )
    # When a short circuit or conditional can return None.
    result = migrate(source)
    # Then retain the operation and surface uncertainty instead of inserting v0.
    assert "return result.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("expression", ["left and right", "left if primary else right"])
@pytest.mark.parametrize("holder", [False, True])
def test_routes_join_when_parameters_have_nonnullable_client_contracts(
    expression: str, holder: bool
) -> None:
    # Given explicit non-null client inputs, in a function or a holder initializer.
    source = "from daytona import Daytona\n" + (
        "class Service:\n"
        "    def __init__(self, left: Daytona, right: Daytona, primary):\n"
        f"        self.client = {expression}\n"
        "    def run(self): return self.client.create()\n"
        if holder
        else "def run(left: Daytona, right: Daytona, primary):\n"
        f"    client = {expression}\n    return client.create()\n"
    )
    # When joining only values whose contracts exclude None.
    result = migrate(source)
    # Then the same proof survives ordinary and class-wide store analysis.
    assert "client.v0.create()" in result.source
    assert not result.items


def test_reviews_join_when_factory_return_is_nullable() -> None:
    # Given a factory whose return annotation does not exclude None.
    source = (
        "from typing import Optional\nfrom daytona import Daytona\n"
        "def maybe() -> Optional[Daytona]: return None\n"
        "client = maybe() if primary else Daytona()\nclient.create()\n"
    )
    # When joining a factory result with a definitely non-null constructor.
    result = migrate(source)
    # Then the factory's client kind alone cannot authorize v0.
    assert "\nclient.create()\n" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_reviews_join_when_nonnull_alias_is_rebound_to_nullable_input() -> None:
    # Given an initially non-null alias that is replaced before the join.
    source = (
        "from typing import Optional\nfrom daytona import Daytona\n"
        "def run(optional: Optional[Daytona], primary):\n"
        "    client = Daytona()\n    client = optional\n"
        "    result = client and Daytona()\n    return result.create()\n"
    )
    # When the binding is invalidated by the nullable write.
    result = migrate(source)
    # Then the old non-null evidence cannot survive the replacement.
    assert "return result.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
