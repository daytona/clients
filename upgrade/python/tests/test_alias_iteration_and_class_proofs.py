# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Alias order, typed iteration and non-instance types retain bounded proofs."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body",
    [
        'def run(d: "Dep"): d.create()\nDep = Daytona',
        "def run(d: Dep): d.create()\nDep = Daytona",
        'def run(d: "Dep"): d.create()\nDep: TypeAlias = "Daytona"',
        "def run(d: dict[str, Daytona]):\n    for k, value in d.items(): value.create()",
        "def run(d: dict[Daytona, str]):\n    for value in d: value.create()",
        "def run(d: dict[Daytona, str]):\n    for value in d.keys(): value.create()",
        "def run(d: dict[Daytona, str]):\n    for value, v in d.items(): value.create()",
        "def run(pair: tuple[Daytona, Daytona]):\n    value, other = pair\n    value.create()",
        "def clients() -> Generator[Daytona, None, None]:\n    yield Daytona()\nfor value in clients(): value.create()",
        "def clients() -> Iterator[Daytona]:\n    yield Daytona()\nfor value in clients(): value.create()",
        "def run(clients: list[Daytona]):\n    intermediate = [d for d in clients]\n    for value in intermediate: value.create()",
        "def run(clients: list[Daytona]):\n    intermediate = (d for d in clients)\n    for value in intermediate: value.create()",
        "if condition:\n    Dep = Daytona\nelse:\n    Dep = str\ndef run(value: Dep): value.create()",
    ],
)
def test_routes_or_reports_when_client_evidence_crosses_supported_flow(
    body: str,
) -> None:
    # Given each previously silent client-bearing path.
    source = (
        "from daytona import Daytona\n"
        "from typing import TypeAlias, Generator, Iterator\n" + body + "\n"
    )
    # When migrating the moved member.
    result = migrate(source)
    # Then a safe rewrite or a manual diagnostic prevents a silent success.
    if result.items:
        assert [item["kind"] for item in result.items] == ["fixme"]
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    else:
        assert ".v0.create()" in result.source


@pytest.mark.parametrize(
    "annotation", ["type[Daytona]", "Type[Daytona]", "Callable[[Daytona], str]"]
)
@pytest.mark.parametrize("name", ["value", "client"])
def test_preserves_receiver_when_annotation_is_not_an_instance(
    annotation: str, name: str
) -> None:
    # Given class objects and callables, not client instances.
    source = (
        "from daytona import Daytona\nfrom typing import Type, Callable\n"
        f"def run({name}: {annotation}): {name}.create()\n"
    )
    # When inspecting a moved instance member on the non-instance.
    result = migrate(source)
    # Then no instance namespace or instance advice is attached to it.
    assert result.source == source
    assert result.items == ()


def test_routes_instance_when_typed_class_is_constructed() -> None:
    # Given a class parameter whose construction returns a client instance.
    source = (
        "from daytona import Daytona\ndef run(cls: type[Daytona]): cls().create()\n"
    )
    # When migrating the constructed instance's call.
    result = migrate(source)
    # Then the namespace belongs to the instance, never the class object.
    assert result.source == source.replace("cls().create()", "cls().v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "def run(d: dict[str, Daytona]):\n    for key, value in d.items(): key.create()",
        "def run(d: dict[Daytona, str]):\n    for key, value in d.items(): value.create()",
        "def run(d: dict[Daytona, str]):\n    for value in d.values(): value.create()",
        "def run(clients: list[Daytona]):\n    intermediate = [str(d) for d in clients]\n    for value in intermediate: value.create()",
        "def run(clients: list[Daytona]):\n    intermediate = [d for d in clients for d in unrelated]\n    for value in intermediate: value.create()",
    ],
)
def test_preserves_nonclient_when_collection_position_has_no_client_evidence(
    body: str,
) -> None:
    # Given unrelated keys, values, or comprehension results beside clients.
    source = "from daytona import Daytona\n" + body + "\n"
    # When analyzing the position used by the call.
    result = migrate(source)
    # Then client evidence cannot leak to the unrelated position.
    assert result.source == source
    assert result.items == ()
