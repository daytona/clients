# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective writes and subscript receiver identity regression witnesses."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize("receiver", ["obj", "holder.obj"])
def test_client_is_unproven_when_setattr_replaces_instance_dictionary(
    receiver: str,
) -> None:
    # Given multiple proven fields replaced together through setattr.
    source = (
        "from daytona import Daytona\n"
        f"{receiver}.client = Daytona()\n{receiver}.backup = Daytona()\n"
        f"setattr({receiver}, '__dict__', {{'client': Other(), 'backup': Other()}})\n"
        f"{receiver}.client.create()\n{receiver}.backup.create()\n"
    )
    # When migrating after the dictionary replacement.
    result = migrate(source)
    # Then neither replaced field retains its automatic rewrite proof.
    assert ".v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("dictionary", ["vars(self)", "self.__dict__"])
@pytest.mark.parametrize(
    "arguments",
    ["client=Other()", "{'client': Other()}", "fields", "**fields"],
)
@pytest.mark.parametrize("separate_method", [False, True])
def test_client_is_unproven_when_dictionary_update_can_replace_field(
    dictionary: str, arguments: str, separate_method: bool
) -> None:
    # Given a proven instance client and a named or unknown dictionary update.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        self.client = Daytona()\n"
        + ("    def replace(self, fields):\n" if separate_method else "")
        + f"        {dictionary}.update({arguments})\n"
        + ("    def run(self):\n" if separate_method else "")
        + "        self.client.create()\n"
    )
    # When migrating the later call, including the class-wide proof path.
    result = migrate(source)
    # Then a potentially replaced field remains a manual migration.
    assert "self.client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("dictionary", ["vars(self)", "self.__dict__"])
@pytest.mark.parametrize("arguments", ["other=Other()", "{'other': Other()}", ""])
def test_client_stays_proven_when_update_cannot_replace_field(
    dictionary: str, arguments: str
) -> None:
    # Given an update whose keys are known and do not include the client.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        self.client = Daytona()\n"
        f"    def replace(self):\n        {dictionary}.update({arguments})\n"
        "    def run(self):\n        self.client.create()\n"
    )
    # When collecting instance writes.
    result = migrate(source)
    # Then the independent client still migrates automatically.
    assert "self.client.v0.create()" in result.source


def test_client_stays_proven_when_nested_subscript_mutates_another_receiver() -> None:
    # Given a deferred client call and a later write to a sibling cache object.
    source = (
        "from daytona import Daytona\nclass Holder: pass\n"
        "holder = Holder()\nalias = holder\n"
        "holder.client = Daytona()\nalias.other_client = Daytona()\n"
        "def run():\n    holder.client.create()\n    alias.other_client.create()\n"
        "holder.cache['x'] = value\n"
    )
    # When invalidating the receiver of __setitem__, not its parent object.
    result = migrate(source)
    # Then unrelated members on the holder and its alias remain proven.
    assert result.source == source.replace(".create()", ".v0.create()")


def test_client_is_unproven_when_subscript_mutates_its_actual_receiver() -> None:
    # Given a custom cache whose __setitem__ can replace its own client field.
    source = (
        "from daytona import Daytona\nholder.cache.client = Daytona()\n"
        "def run():\n    holder.cache.client.create()\n"
        "holder.cache['x'] = value\n"
    )
    # When migrating a deferred use after that mutation.
    result = migrate(source)
    # Then the cache's own field is not trusted.
    assert ".v0.create()" not in result.source
