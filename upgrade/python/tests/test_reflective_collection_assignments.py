# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective attribute stores preserve collection receiver evidence."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "target",
    ['vars(self)["pool"]', 'builtins.vars(self)["pool"]', 'self.__dict__["pool"]'],
)
@pytest.mark.parametrize("scope", ["local", "class"])
def test_collection_call_is_rewritten_when_reflective_store_is_unshadowed(
    target: str, scope: str
) -> None:
    # Given a literal client collection stored through an instance dictionary.
    body = (
        f"def run(self):\n    {target} = [Daytona()]\n    self.pool[0].create()\n"
        if scope == "local"
        else f"class Owner:\n    def __init__(self):\n        {target} = [Daytona()]\n"
        "    def run(self):\n        self.pool[0].create()\n"
    )
    source = "from daytona import Daytona\nimport builtins\n" + body
    # When upgrading the consumer of that collection.
    result = migrate(source)
    # Then the proven element call is namespaced without manual review.
    assert result.source == source.replace(
        "self.pool[0].create()", "self.pool[0].v0.create()"
    )
    assert result.items == ()
