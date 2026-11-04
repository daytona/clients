# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Replacing a moved member requires review rather than bypassing the stub."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize("owner", ["client", "Daytona", "Alias"])
@pytest.mark.parametrize(
    "write",
    [
        "{owner}.create = stub",
        "setattr({owner}, 'create', stub)",
        "object.__setattr__({owner}, 'create', stub)",
    ],
)
def test_member_stays_manual_when_moved_member_is_replaced(
    owner: str, write: str
) -> None:
    # Given a proven client or SDK constructor receiving a replacement method.
    source = (
        "from daytona import Daytona\nclient = Daytona()\nAlias = Daytona\n"
        + write.format(owner=owner)
        + "\nclient.create()\n"
    )
    # When migrating both the write and its later consumer.
    result = migrate(source)
    # Then the stub is never bypassed and the write itself is actionable.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "mock-target" for item in result.items)
    assert "FIXME(daytona-upgrade):" in result.source


@pytest.mark.parametrize(
    "write",
    [
        "client.extra = stub",
        "setattr(client, 'extra', stub)",
        "object.__setattr__(client, 'extra', stub)",
    ],
)
def test_client_moves_when_written_member_did_not_move(write: str) -> None:
    # Given an unrelated attribute store on the same client.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        + write
        + "\nclient.create()\n"
    )
    # When migrating the client call.
    result = migrate(source)
    # Then an unrelated write does not erase the receiver's proof.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


def test_unrelated_receiver_stays_clean_when_create_is_replaced() -> None:
    # Given a method write without Daytona-linked receiver evidence.
    source = "from daytona import Daytona\nother = Other()\nother.create = stub\nother.create()\n"
    # When migrating the unrelated receiver.
    result = migrate(source)
    # Then no SDK namespace or diagnostic is invented.
    assert result.source == source
    assert result.items == ()
