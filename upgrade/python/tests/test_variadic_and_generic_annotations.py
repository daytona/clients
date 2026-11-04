# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""PEP 646 varargs, type aliases and PEP 695/696 type parameters migrate."""

from __future__ import annotations

import sys

import pytest
from test_flow import migrate

needs_311 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="PEP 646 star syntax needs Python 3.11"
)
needs_312 = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="PEP 695 type params need Python 3.12"
)
needs_313 = pytest.mark.skipif(
    sys.version_info < (3, 13), reason="PEP 696 type-param defaults need Python 3.13"
)


@needs_311
def test_rewrites_pep646_starred_vararg_annotation() -> None:
    # Given a variadic parameter whose PEP 646 star annotation quotes an SDK type.
    source = (
        "import daytona\n"
        'def g(*args: *tuple["daytona.Sandbox", int]) -> None:\n'
        "    print(args)\n"
    )
    # When upgrading the function.
    result = migrate(source)
    # Then the quoted type inside the starred annotation still moves.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


@needs_311
def test_rewrites_pep646_starred_vararg_neighbours() -> None:
    # Given the Unpack spelling and an ordinary **kwargs annotation alongside it.
    source = (
        "import daytona\n"
        "from typing import Unpack\n"
        'def g(*args: Unpack[tuple["daytona.Sandbox"]], **kwargs: "daytona.Sandbox") -> None:\n'
        "    print(args, kwargs)\n"
    )
    # When upgrading the function.
    result = migrate(source)
    # Then both quoted operands move.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


@pytest.mark.parametrize(
    "expression",
    [
        'cast(*[str, "daytona.Sandbox"])',
        'ForwardRef(*["daytona.Sandbox"])',
        'TypeVar("T", *["daytona.Sandbox"])',
        'NewType("N", *["daytona.Sandbox"])',
    ],
)
def test_preserves_starred_call_argument_unpacks(expression: str) -> None:
    # Given a runtime unpack into a typing call, not an annotation.
    source = (
        "import daytona\nfrom typing import cast, ForwardRef, TypeVar, NewType\n"
        f"x = {expression}\n"
    )
    # When upgrading type references.
    result = migrate(source)
    # Then the starred runtime argument is left alone, not read as a type.
    assert result.source == source


@needs_312
@pytest.mark.parametrize("keyword", ["def", "class"])
def test_rewrites_unquoted_type_parameter_bound(keyword: str) -> None:
    # Given a PEP 695 function or class whose type-parameter bound is unquoted.
    header = (
        "def f1[T: daytona.Sandbox](x: T) -> T: ...\n"
        if keyword == "def"
        else "class C1[T: daytona.Sandbox]: ...\n"
    )
    source = "import daytona\n" + header
    # When upgrading the declaration.
    result = migrate(source)
    # Then the bound proves the attribute just like an ordinary annotation.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


@needs_312
@pytest.mark.parametrize("keyword", ["def", "class"])
def test_rewrites_quoted_type_parameter_bound(keyword: str) -> None:
    # Given a PEP 695 function or class whose type-parameter bound is quoted.
    header = (
        'def h[T: "daytona.Sandbox"](x): ...\n'
        if keyword == "def"
        else 'class K[T: "daytona.Sandbox"]: ...\n'
    )
    source = "import daytona\n" + header
    # When upgrading the declaration.
    result = migrate(source)
    # Then the quoted bound string still moves.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


@needs_312
def test_rewrites_type_parameter_constraints_quoted_and_unquoted() -> None:
    # Given a constraint tuple mixing a quoted and an unquoted SDK type.
    source = (
        'import daytona\ndef f[T: (daytona.Sandbox, "daytona.Image")](x: T) -> T: ...\n'
    )
    # When upgrading the declaration.
    result = migrate(source)
    # Then both constraint members move.
    assert result.source == source.replace(
        "daytona.Sandbox", "daytona.v0.Sandbox"
    ).replace("daytona.Image", "daytona.v0.Image")


@needs_312
def test_rewrites_quoted_type_alias_statement() -> None:
    # Given a PEP 695 `type` alias whose right-hand side is quoted.
    source = 'import daytona\ntype Y = "daytona.Sandbox"\n'
    # When upgrading the alias.
    result = migrate(source)
    # Then the quoted alias target moves.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


@pytest.mark.parametrize(
    "declaration,marker",
    [
        ("from typing import TypeAlias", "TypeAlias"),
        ("from typing import TypeAlias as TA", "TA"),
        ("import typing as t", "t.TypeAlias"),
        ("from typing_extensions import TypeAlias as TA", "TA"),
    ],
)
def test_rewrites_quoted_typing_type_alias_assignment(
    declaration: str, marker: str
) -> None:
    # Given a typing.TypeAlias-annotated assignment whose value is quoted.
    source = f'import daytona\n{declaration}\nX: {marker} = "daytona.Sandbox"\n'
    # When upgrading the assignment.
    result = migrate(source)
    # Then the quoted alias target moves.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


def test_preserves_quoted_assignment_when_type_alias_is_shadowed() -> None:
    # Given a local name that shadows typing.TypeAlias with an unrelated value.
    source = (
        'import daytona\nTypeAlias = "not typing"\nX: TypeAlias = "daytona.Sandbox"\n'
    )
    # When upgrading the assignment.
    result = migrate(source)
    # Then the shadowed annotation is not trusted as a type-alias marker.
    assert result.source == source


@needs_313
@pytest.mark.parametrize(
    "declaration",
    [
        'def f[T = "daytona.Sandbox"](x: T = None) -> T: ...',
        'class C[T = "daytona.Sandbox"]: ...',
        'type Alias[T = "daytona.Sandbox"] = list[T]',
        'def f[**P = ["daytona.Sandbox"]](): ...',
        'def f[*Ts = *tuple["daytona.Sandbox"]](): ...',
    ],
)
def test_rewrites_type_parameter_default_value(declaration: str) -> None:
    # Given a PEP 696 type-parameter default that quotes an SDK type.
    source = f"import daytona\n{declaration}\n"
    # When upgrading the declaration.
    result = migrate(source)
    # Then the quoted default still moves.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")
