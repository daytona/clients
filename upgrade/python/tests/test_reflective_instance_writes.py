# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective instance writes invalidate automatic client receiver proofs."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize("separate_method", [False, True])
@pytest.mark.parametrize(
    "write",
    [
        'self.__dict__["client"] = Other()',
        'vars(self)["client"] = Other()',
        "self.__dict__[name] = Other()",
        "vars(self)[name] = Other()",
        'setattr(self, "client", Other())',
        "setattr(self, name, Other())",
        '(self.__dict__["client"], other) = (Other(), None)',
        'self.__dict__["client"]: object = Other()',
    ],
)
def test_client_is_unproven_when_reflective_store_replaces_instance_attribute(
    write: str, separate_method: bool
) -> None:
    # Given a client proof from initialization and a reflective replacement.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        self.client = Daytona()\n"
        + ("    def replace(self, name):\n" if separate_method else "")
        + f"        {write}\n"
        + ("    def run(self):\n" if separate_method else "")
        + "        return self.client.create()\n"
    )
    # When upgrading either a later local call or a separate method's call.
    result = migrate(source)
    # Then the replacement keeps its own API and gets the existing manual item.
    assert "self.client.v0.create()" not in result.source
    assert "self.client.create()" in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "write",
    [
        'self.__dict__["other"] = Other()',
        'vars(self)["other"] = Other()',
        'setattr(self, "other", Other())',
    ],
)
def test_client_proof_survives_when_literal_reflective_store_targets_other_attribute(
    write: str,
) -> None:
    # Given a literal key which cannot replace the client attribute.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        self.client = Daytona()\n"
        f"    def replace(self):\n        {write}\n"
        "    def run(self):\n        return self.client.create()\n"
    )
    # When joining class-wide attribute writes.
    result = migrate(source)
    # Then unrelated literal writes do not erase the client proof.
    assert "self.client.v0.create()" in result.source
