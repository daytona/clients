# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Typed iteration transfers only established element evidence to targets."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "annotation",
    [
        "Sequence[Daytona]",
        "list[Daytona]",
        "Iterable[Daytona]",
        '"list[Daytona]"',
        "Sequence['Daytona']",
        "Sequence[Optional[Daytona]]",
        "C[Daytona]",
        "Clients",
        "tuple[Daytona, ...]",
        "tuple[Daytona, Daytona]",
        "T[Daytona, ...]",
    ],
)
@pytest.mark.parametrize(
    "body",
    ["for d in clients:\n        d.create()", "return [d.create() for d in clients]"],
)
def test_routes_element_when_collection_annotation_proves_client(
    annotation: str, body: str
) -> None:
    # Given a homogeneous client collection with either iteration surface.
    source = (
        "from daytona import Daytona\nfrom typing import Sequence, Iterable, Optional, Tuple as T\n"
        "from collections.abc import Sequence as C\n"
        "Clients = Sequence[Daytona]\n"
        f"def launch(clients: {annotation}):\n    {body}\n"
    )
    # When migrating the element's call.
    result = migrate(source)
    # Then the target inherits the collection's proven client element type.
    assert result.source == source.replace("d.create()", "d.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("annotation", ["Daytona", '"Daytona"', "Optional[Daytona]"])
@pytest.mark.parametrize(
    "body",
    ["for d in clients:\n        d.create()", "return [d.create() for d in clients]"],
)
def test_routes_element_when_varargs_are_client_typed(
    annotation: str, body: str
) -> None:
    # Given typed variadic positional arguments, whose local value is a tuple.
    source = (
        "from daytona import Daytona\nfrom typing import Optional\n"
        + f"def launch(*clients: {annotation}):\n    {body}\n"
    )
    # When migrating iteration over that tuple.
    result = migrate(source)
    # Then argument annotations prove elements, never the tuple itself.
    assert result.source == source.replace("d.create()", "d.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("annotation", ["Daytona | str", "Unknown[Daytona]"])
def test_reports_element_when_varargs_client_type_is_uncertain(annotation: str) -> None:
    # Given positional arguments whose annotation mentions, but cannot prove, clients.
    source = (
        "from daytona import Daytona\n"
        + f"def launch(*clients: {annotation}):\n    return [d.create() for d in clients]\n"
    )
    # When migrating calls on the tuple's elements.
    result = migrate(source)
    # Then uncertainty reaches the element call rather than silently disappearing.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "annotation",
    [
        "Sequence[Daytona | str]",
        "Iterable[Unknown[Daytona]]",
        "list[MissingDaytona]",
        "tuple[Daytona, str]",
    ],
)
@pytest.mark.parametrize(
    "body",
    ["for d in clients:\n        d.create()", "return [d.create() for d in clients]"],
)
def test_reports_element_when_collection_client_type_is_uncertain(
    annotation: str, body: str
) -> None:
    # Given a mixed or unsupported container annotation mentioning Daytona.
    source = (
        "from daytona import Daytona\nfrom typing import Sequence, Iterable\n"
        + f"def launch(clients: {annotation}):\n    {body}\n"
    )
    # When examining a moved member on its iteration target.
    result = migrate(source)
    # Then no redirect is guessed, but the call cannot be silently missed.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("call", ["k.upper()", "k.create()"])
def test_preserves_keys_when_dictionary_values_are_clients(call: str) -> None:
    # Given a dictionary whose client annotation describes values, not string keys.
    source = (
        "from daytona import Daytona\n"
        f"def launch(d: dict[str, Daytona]):\n    for k in d:\n        {call}\n"
    )
    # When migrating a call on a key.
    result = migrate(source)
    # Then value evidence never causes a key rewrite or a manual item.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "body", ["d[k].create()", "for value in d.values():\n        value.create()"]
)
def test_reports_values_when_dictionary_annotation_mentions_clients(body: str) -> None:
    # Given client values accessed through a lookup or value iteration.
    source = (
        f"from daytona import Daytona\ndef launch(d: dict[str, Daytona]):\n    {body}\n"
    )
    # When migrating the value's moved member.
    result = migrate(source)
    # Then dictionary values retain the existing conservative manual-review rule.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "source_body",
    [
        "def launch(clients: list[str]):\n    return [d.create() for d in clients]",
        "def launch(*clients: Daytona):\n    clients.create()",
        "def launch(**clients: Daytona):\n    return [d.create() for d in clients]",
        "def launch(clients: list[Daytona]):\n    clients = []\n    return [d.create() for d in clients]",
        "def launch(clients: list[Daytona]):\n    return [d.create() for d in clients for d in unrelated]",
        "def launch(clients: list[Daytona]):\n    clients.append(unrelated)\n    return [d.create() for d in clients]",
    ],
)
def test_preserves_calls_when_target_is_not_proven_client(source_body: str) -> None:
    # Given a nonclient binding or a collection whose element evidence was killed.
    source = "from daytona import Daytona\n" + source_body + "\n"
    # When analyzing iteration and variadic local bindings.
    result = migrate(source)
    # Then collection-level annotations never leak past rebinding or scope kills.
    assert "d.v0.create()" not in result.source
    assert "clients.v0.create()" not in result.source
