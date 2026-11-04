# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Collection copies and starred targets retain existing receiver evidence."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "wrapper",
    ["dict(**clients)", "{**clients} | other", "other | {**clients}", "dict(clients)"],
)
@pytest.mark.parametrize("annotation", ["Daytona", "Wrapped[Daytona]", "Client"])
def test_reviews_dictionary_when_copy_retains_client_evidence(
    wrapper: str, annotation: str
) -> None:
    # Given typed mapping values and an unknown mapping that may override entries.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def launch(clients: dict[str, {annotation}], other):\n"
        f"    copied = {wrapper}\n    for value in copied.values(): value.get('url')\n"
    )
    # When inspecting a call through a dictionary copy or union.
    result = migrate(source)
    # Then dictionary lookups keep review evidence, never an exact receiver proof.
    assert [item["kind"] for item in result.items] == (
        [] if annotation == "Client" else ["fixme"]
    )
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "target, call",
    [
        ("a, *rest", "a.get('url')"),
        ("a, *rest", "rest[0].get('url')"),
        ("*rest, a", "a.get('url')"),
        ("a, *rest, z", "z.get('url')"),
    ],
)
@pytest.mark.parametrize(
    "annotation",
    ["tuple[Daytona, ...]", "tuple[Wrapped[Daytona], ...]", "tuple[Client, ...]"],
)
def test_retains_receiver_when_unpacking_homogeneous_tuple_with_star(
    target: str, call: str, annotation: str
) -> None:
    # Given homogeneous proven, uncertain, or unrelated tuple elements.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def launch(clients_tuple: {annotation}):\n    {target} = clients_tuple\n    {call}\n"
    )
    # When extracting a fixed position or the starred remainder.
    result = migrate(source)
    # Then homogeneous proof survives, while uncertainty remains manual review.
    assert [item["kind"] for item in result.items] == (
        ["fixme"] if "Wrapped" in annotation else []
    )
    expected = (
        source.replace(".get(", ".v0.get(")
        if annotation == "tuple[Daytona, ...]"
        else source
    )
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))


@pytest.mark.parametrize(
    "call, review",
    [("a.get('url')", False), ("rest[0].get('url')", True), ("z.get('url')", False)],
)
def test_retains_positions_when_unpacking_heterogeneous_tuple_with_star(
    call: str, review: bool
) -> None:
    # Given client evidence only in the middle position of a heterogeneous tuple.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def launch(clients_tuple: tuple[Client, Daytona, Client]):\n"
        f"    a, *rest, z = clients_tuple\n    {call}\n"
    )
    # When observing one of the fixed positions or the starred remainder.
    result = migrate(source)
    # Then unrelated positions stay clean and only client-bearing slots need review.
    assert [item["kind"] for item in result.items] == (["fixme"] if review else [])
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "declaration", ["dict = factory", "def dict(**kwargs): return other"]
)
def test_ignores_copy_when_dict_constructor_is_shadowed(declaration: str) -> None:
    # Given a user callable named dict, which need not preserve mapping contents.
    source = (
        "from daytona import Daytona\n"
        f"{declaration}\ndef launch(clients: dict[str, Daytona]):\n"
        "    copied = dict(**clients)\n    for value in copied.values(): value.get('url')\n"
    )
    # When analyzing the arbitrary call rather than a builtin copy.
    result = migrate(source)
    # Then the constructor spelling alone cannot propagate collection evidence.
    assert result.items == ()
    assert result.source == source


@pytest.mark.parametrize("wrapper", ["dict(**clients)", "{**clients} | other"])
def test_preserves_copy_when_mapping_has_no_evidence(wrapper: str) -> None:
    # Given entirely unknown mappings, not client-bearing annotations.
    source = f"def launch(clients, other):\n    copied = {wrapper}\n    for value in copied.values(): value.get('url')\n"
    # When following the copy's values.
    result = migrate(source)
    # Then copy syntax alone does not invent Daytona evidence.
    assert result.items == ()
    assert result.source == source


@pytest.mark.parametrize(
    "call, review", [("key.get('url')", False), ("rest[0].get('url')", True)]
)
def test_retains_pair_evidence_when_items_target_is_starred(
    call: str, review: bool
) -> None:
    # Given dictionary pairs with unrelated keys and uncertain client values.
    source = (
        "from daytona import Daytona\n"
        "def launch(clients: dict[str, Daytona]):\n"
        f"    for key, *rest in clients.items(): {call}\n"
    )
    # When inspecting a fixed key or the list collecting the value position.
    result = migrate(source)
    # Then starred collection retains only the value's manual-review evidence.
    assert [item["kind"] for item in result.items] == (["fixme"] if review else [])
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
