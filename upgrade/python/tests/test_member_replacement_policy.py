# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Method replacement fences dispatch, not independent methods or settings."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "member",
    [
        "create",
        "get",
        "list",
        "start",
        "stop",
        "delete",
        "volume",
        "snapshot",
        "secret",
        "warm_pool",
    ],
)
@pytest.mark.parametrize(
    "write",
    [
        "client.{member} = stub",
        "setattr(client, '{member}', stub)",
        "object.__setattr__(client, '{member}', stub)",
    ],
)
def test_other_member_moves_when_only_one_member_is_replaced(
    member: str, write: str
) -> None:
    # Given a replaced method/accessor alongside an independent member.
    other = "get" if member == "create" else "create"
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        + write.format(member=member)
        + f"\nclient.{member}()\nclient.{other}()\n"
    )
    # When migrating both subsequent calls.
    result = migrate(source)
    # Then only dispatch of the replaced member is unproven.
    assert f"\nclient.{member}()\n" in result.source
    assert f"\nclient.v0.{other}()\n" in result.source
    assert [item["kind"] for item in result.items] == ["mock-target", "fixme"]


def test_data_setting_moves_when_another_member_is_replaced() -> None:
    # Given a method replacement which does not change the client identity.
    source = "from daytona import Daytona\nclient = Daytona()\nclient.create = stub\nclient.default_language = 'python'\n"
    # When migrating an independent setting change.
    result = migrate(source)
    # Then the setting still reaches the v0 client without its own diagnostic.
    assert "client.v0.default_language = 'python'" in result.source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("member", ["volume", "snapshot", "secret", "warm_pool"])
def test_service_assignment_moves_when_replacement_is_proven_same_service(
    member: str,
) -> None:
    # Given an accessor assigned a service of its existing kind, not a stub.
    source = f"from daytona import Daytona\nclient = Daytona()\nother = Daytona()\nclient.{member} = other.{member}\nclient.create()\n"
    # When preserving the legitimate service assignment.
    result = migrate(source)
    # Then the assignment migrates without losing independent client proofs.
    assert result.source == source.replace(
        f"client.{member}", f"client.v0.{member}"
    ).replace(f"other.{member}", f"other.v0.{member}").replace(
        "client.create()", "client.v0.create()"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "lookup", ["getattr(client, 'create')()", "hasattr(client, 'create')"]
)
def test_lookup_stays_manual_when_queried_member_was_replaced(lookup: str) -> None:
    # Given the same method reached via a binding-proven builtin lookup.
    source = (
        "from daytona import Daytona\nclient = Daytona()\nclient.create = stub\n"
        + lookup
        + "\n"
    )
    # When migrating the lookup after the write.
    result = migrate(source)
    # Then a lookup cannot bypass the replacement either.
    assert lookup in result.source
    assert [item["kind"] for item in result.items] == ["mock-target", "fixme"]
