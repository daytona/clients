# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Runtime calls nested in type syntax do not turn their strings into types."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "statement",
    [
        'x: get_type("daytona.Sandbox")',
        'x: list[get_type("daytona.Sandbox")]',
        'x: Annotated[T, make("daytona.Sandbox")]',
        "x: \"get_type('daytona.Sandbox')\"",
        "x: \"list[get_type('daytona.Sandbox')]\"",
        'x = cast(get_type("daytona.Sandbox"), value)',
        'x = cast(typ=get_type("daytona.Sandbox"), val=value)',
        'x = cast(*[str, "daytona.Sandbox"])',
        'x = TypeVar("T", get_type("daytona.Sandbox"))',
        'x = TypeVar("T", bound=get_type("daytona.Sandbox"))',
        'x = TypeVar("T", default=get_type("daytona.Sandbox"))',
        'x = NewType("N", get_type("daytona.Sandbox"))',
        'x = NewType("N", tp=get_type("daytona.Sandbox"))',
        'x = ParamSpec("P", bound=get_type("daytona.Sandbox"))',
        'x = ParamSpec("P", default=get_type("daytona.Sandbox"))',
    ],
)
def test_preserves_runtime_strings_when_calls_are_nested_in_type_positions(
    statement: str,
) -> None:
    # Given a runtime call or starred runtime argument within type syntax.
    source = (
        "import daytona\nfrom typing import Annotated, cast, TypeVar, NewType, ParamSpec\n"
        + statement
        + "\n"
    )
    # When migrating type references.
    result = migrate(source)
    # Then runtime strings retain their original values.
    assert result.source == source


@pytest.mark.parametrize(
    "annotation",
    [
        '"daytona.Sandbox"',
        'list["daytona.Sandbox"]',
        'dict[str, "daytona.Sandbox"]',
        'Callable[["daytona.Sandbox"], "daytona.Image"]',
        'Annotated["daytona.Sandbox", make("daytona.Sandbox")]',
        "\"Annotated[daytona.Sandbox, make('daytona.Sandbox')]\"",
    ],
)
def test_rewrites_only_type_nodes_when_annotation_contains_nested_syntax(
    annotation: str,
) -> None:
    # Given direct and composite forward references, with optional runtime metadata.
    source = (
        f"import daytona\nfrom typing import Annotated, Callable\nx: {annotation}\n"
    )
    expected = source.replace("daytona.Sandbox", "daytona.v0.Sandbox", 1).replace(
        "daytona.Image", "daytona.v0.Image"
    )
    # When migrating the annotation.
    result = migrate(source)
    # Then type strings move while metadata strings stay unchanged.
    assert result.source == expected
