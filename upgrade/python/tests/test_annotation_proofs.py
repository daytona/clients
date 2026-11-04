# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative receiver and annotation proofs retain client uncertainty."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.collection_proofs import literal_collection_kind
from daytona_upgrade.models import load_mapping
from daytona_upgrade.resolution import ProjectIndex, Resolver


def test_collection_is_unresolved_when_dict_contains_unknown_value() -> None:
    # Given an arbitrary factory result inside a literal dictionary.
    node = ast.parse('{"x": get_client()}', mode="eval").body
    resolver = Resolver(load_mapping(), ProjectIndex({}))
    # When collecting value proofs without evaluating the factory.
    kind = literal_collection_kind(node, lambda value: resolver.infer(value, {}))
    # Then absent evidence cannot certify the dictionary as unrelated to clients.
    assert kind == ""


@pytest.mark.parametrize(
    "values", ['"x": get_client()', '"x": 1, "y": unknown', "**unknown"]
)
def test_preserves_lookup_when_unknown_dict_values_have_no_daytona_link(
    values: str,
) -> None:
    # Given unknown dictionary values without linked Daytona evidence.
    source = (
        f'from daytona import Daytona\nclients = {{{values}}}\nclients["x"].create()\n'
    )
    # When migrating a lookup without value proof.
    result = migrate(source)
    # Then uncertainty alone does not justify a manual item.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "annotation", ["Union[Daytona, str]", "Daytona | str", '"Union[Daytona, str]"']
)
def test_reports_receiver_when_union_contains_unproven_client(annotation: str) -> None:
    # Given an SDK-like external type mixed with an unrelated variant.
    source = f"from mycorp import Daytona\nfrom typing import Union\ndef f(value: {annotation}):\n    value.create()\n"
    # When analyzing the annotation without inspecting the external package.
    result = migrate(source)
    # Then uncertainty survives the union, but never becomes rewrite proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "annotation",
    [
        "\"Literal['Sandbox']\"",
        "\"Annotated[str, 'Sandbox']\"",
        '"Annotated[str, daytona.Sandbox]"',
        "\"list[Literal['Sandbox']]\"",
        "\"L['Sandbox']\"",
        "\"A[str, 'Sandbox']\"",
    ],
)
def test_preserves_annotation_when_sdk_name_is_metadata(annotation: str) -> None:
    # Given quoted value metadata, including binding-proven typing aliases.
    source = f"import daytona\nfrom typing import Literal, Annotated, Literal as L, Annotated as A\ndef f(value: {annotation}): pass\n"
    # When scanning strings for moved SDK types.
    result = migrate(source)
    # Then metadata neither changes nor creates a spurious strict-mode item.
    assert result.source == source
    assert result.items == ()


def test_rewrites_annotation_when_annotated_type_needs_migration() -> None:
    # Given a real moved type before Annotated's non-type extras.
    source = "import daytona\nfrom typing import Annotated\ndef f(value: \"Annotated[daytona.Sandbox, 'safe']\"): pass\n"
    # When analyzing a compound annotation with a binding-proven type.
    result = migrate(source)
    # Then the type moves while its metadata remains untouched.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")
    assert result.items == ()


@pytest.mark.parametrize("binding", ["import plugin", "from mycorp import plugin"])
def test_reports_conflict_when_client_annotation_initializes_from_module(
    binding: str,
) -> None:
    # Given a bare imported module instead of a client instance.
    source = f"from daytona import Daytona\n{binding}\nclient: Daytona = plugin\nclient.create()\n"
    # When applying annotated-assignment proof rules.
    result = migrate(source)
    # Then the annotation cannot redirect a known conflicting runtime value.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "parameter", ['*xs: "daytona.Sandbox"', '**kw: "daytona.Sandbox"']
)
@pytest.mark.parametrize("prefix", ["def", "async def"])
def test_moves_forward_reference_when_parameter_is_variadic(
    parameter: str, prefix: str
) -> None:
    # Given a variadic parameter with a real type annotation.
    source = f"import daytona\n{prefix} f({parameter}): pass\n"
    # When migrating its string forward reference.
    result = migrate(source)
    # Then variadic annotations use the same namespace as normal parameters.
    assert result.source == source.replace('"daytona.Sandbox"', '"daytona.v0.Sandbox"')
    assert result.items == ()


@pytest.mark.parametrize("annotation", ["Daytona", '"Daytona"', "Union[Daytona, None]"])
def test_reports_factory_when_decorator_wraps_unproven_return(annotation: str) -> None:
    # Given a factory whose annotation is already uncertain before decoration.
    source = f"from mycorp import Daytona, adapt\nfrom typing import Union\n@adapt\ndef factory() -> {annotation}: return unknown()\nvalue = factory()\nvalue.create()\n"
    # When consuming the decorated factory without a client-like variable name.
    result = migrate(source)
    # Then decoration cannot erase the actionable uncertainty marker.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
