# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal builtin lookups use the same receiver proof as direct attributes."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize("member", ["create", "snapshot", "warm_pool"])
@pytest.mark.parametrize("receiver", ["Daytona()", "client", "owner.client"])
def test_lookup_routes_when_literal_member_has_proven_receiver(
    builtin: str, member: str, receiver: str
) -> None:
    # Given literal lookups, including detached values and getattr's default.
    source = (
        "from daytona import Daytona\nclient = Daytona()\nowner.client = Daytona()\n"
        f"result = {builtin}({receiver}, '{member}'"
        + (", None)\n" if builtin == "getattr" else ")\n")
    )
    # When migrating the actual builtin call.
    result = migrate(source)
    # Then the receiver alone gains the namespace and reruns make no edits.
    assert f"{builtin}({receiver}.v0, '{member}'" in result.source
    assert migrate(result.source).source == result.source


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
def test_lookup_stays_when_generic_receiver_has_no_daytona_link(builtin: str) -> None:
    # Given an unannotated generic client unrelated to the SDK import.
    source = f"from daytona import Daytona\ndef run(client):\n    return {builtin}(client, 'create')\n"
    # When inspecting a literal moved member.
    result = migrate(source)
    # Then generic spelling cannot justify a manual item or rewrite.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize(
    "shadow",
    [
        "{builtin} = custom\nresult = {builtin}(client, 'create')\n",
        "def run({builtin}):\n    return {builtin}(client, 'create')\n",
        "from custom import {builtin}\nresult = {builtin}(client, 'create')\n",
        "def {builtin}(*args):\n    return args\nresult = {builtin}(client, 'create')\n",
        "def run():\n    result = {builtin}(client, 'create')\n    {builtin} = custom\n",
    ],
)
def test_lookup_stays_when_builtin_is_shadowed(builtin: str, shadow: str) -> None:
    # Given a name that belongs to user code rather than builtins.
    source = "from daytona import Daytona\nclient = Daytona()\n" + shadow.format(
        builtin=builtin
    )
    # When migrating the source.
    result = migrate(source)
    # Then no namespace is inserted into user-defined lookup arguments.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize("member", ["close", "v0", "v2"])
def test_lookup_stays_when_member_has_not_moved(builtin: str, member: str) -> None:
    # Given a root-owned or already namespaced member.
    source = f"from daytona import Daytona\nresult = {builtin}(Daytona(), '{member}')\n"
    # When migrating the lookup.
    result = migrate(source)
    # Then it remains at the root.
    assert result.source == source


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize("qualified", [False, True])
def test_lookup_routes_when_builtin_has_import_alias(
    builtin: str, qualified: bool
) -> None:
    # Given the same builtin reached through its module or an imported alias.
    imported = (
        "import builtins as b\n"
        if qualified
        else f"from builtins import {builtin} as lookup\n"
    )
    callee = f"b.{builtin}" if qualified else "lookup"
    source = f"from daytona import Daytona\n{imported}result = {callee}(Daytona(), 'create')\n"
    # When resolving the function binding rather than its spelling.
    result = migrate(source)
    # Then the builtin's literal receiver is routed.
    assert f"{callee}(Daytona().v0, 'create')" in result.source


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
def test_lookup_stays_when_builtin_module_is_mutated(builtin: str) -> None:
    # Given a process builtin replaced with a user-defined lookup.
    source = (
        f"from daytona import Daytona\nimport builtins\nbuiltins.{builtin} = custom\n"
        f"result = {builtin}(Daytona(), 'create')\n"
    )
    # When the shared project index records the mutation.
    result = migrate(source)
    # Then rewriting cannot assume builtin semantics.
    assert result.source == source


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
def test_lookup_stays_when_receiver_is_unrelated(builtin: str) -> None:
    # Given a known non-client receiver whose member happens to have moved in SDK.
    source = f"from daytona import Daytona\nclass Other:\n    pass\nclient = Other()\nresult = {builtin}(client, 'create')\n"
    # When receiver identity rules out the root SDK client.
    result = migrate(source)
    # Then the user-defined lookup is not rewritten.
    assert f"{builtin}(client, 'create')" in result.source
    assert result.items == ()


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize(
    "receiver",
    ["await acquire_client()", "(client := Daytona())"],
)
def test_lookup_preserves_receiver_precedence_when_adding_namespace(
    builtin: str, receiver: str
) -> None:
    # Given a proven client expression that needs grouping before attribute access.
    source = (
        "from daytona import Daytona\nleft = right = Daytona()\n"
        "async def acquire_client() -> Daytona:\n    return Daytona()\n"
        f"async def run():\n    return {builtin}({receiver}, 'create')\n"
    )
    # When routing the lookup through the compatibility namespace.
    result = migrate(source)
    # Then the namespace belongs to the entire receiver, not one operand.
    expected = source.replace(f"{builtin}({receiver},", f"{builtin}(({receiver}).v0,")
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))
    assert migrate(result.source).source == result.source


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize("name", ['"cre" "ate"', '("war" "m_" "pool")'])
def test_lookup_routes_when_member_name_is_concatenated(
    builtin: str, name: str
) -> None:
    # Given adjacent literals naming a moved client member.
    source = f"from daytona import Daytona\nclient = Daytona()\nresult = {builtin}(client, {name})\n"
    # When resolving the statically known name.
    result = migrate(source)
    # Then literal spelling is preserved while the receiver is routed.
    assert result.source == source.replace(
        f"{builtin}(client,", f"{builtin}(client.v0,"
    )


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
@pytest.mark.parametrize("name", ["member", 'f"{member}"', '"cre" + suffix'])
def test_lookup_reports_when_proven_client_member_name_is_dynamic(
    builtin: str, name: str
) -> None:
    # Given a client lookup whose member might need the compatibility namespace.
    source = f"from daytona import Daytona\nclient = Daytona()\nresult = {builtin}(client, {name})\n"
    # When no static member name can be proven.
    result = migrate(source)
    # Then review is required without guessing a runtime member.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert f"{builtin}(client, {name})" in result.source
    assert migrate(result.source).source == result.source
