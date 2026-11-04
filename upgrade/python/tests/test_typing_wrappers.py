# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only binding-proven typing wrappers establish client annotation evidence."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "wrapper",
    ["Optional[Daytona]", "Union[Daytona, None]", "Annotated[Daytona, metadata]"],
)
@pytest.mark.parametrize(
    "binding,prefix",
    [
        ("import foo", "foo."),
        ("from foo import Optional, Union, Annotated", ""),
        (
            "from typing import Optional, Union, Annotated\nOptional = other\nUnion = other\nAnnotated = other",
            "",
        ),
    ],
)
def test_requires_review_when_typing_wrapper_binding_is_unproven(
    wrapper: str, binding: str, prefix: str
) -> None:
    # Given an unrelated or shadowed wrapper with a familiar typing name.
    source = (
        f"from daytona import Daytona\n{binding}\n"
        f"def launch(x: {prefix}{wrapper}):\n    x.create()\n"
    )
    # When migrating the annotated receiver.
    result = migrate(source)
    # Then the spelling alone cannot justify redirecting an unrelated instance.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("module", ["typing", "typing_extensions"])
@pytest.mark.parametrize(
    "binding,annotation",
    [
        ("import {module} as t", "t.Optional[Daytona]"),
        ("import {module} as t", "t.Union[Daytona, None]"),
        ("import {module} as t", "t.Annotated[Daytona, metadata]"),
        ("from {module} import Optional as O", "O[Daytona]"),
        ("from {module} import Union as U", "U[Daytona, None]"),
        ("from {module} import Annotated as A", "A[Daytona, metadata]"),
    ],
)
def test_routes_client_when_typing_wrapper_binding_is_proven(
    module: str, binding: str, annotation: str
) -> None:
    # Given imported or module-qualified typing helpers, including aliases.
    source = (
        f"from daytona import Daytona\n{binding.format(module=module)}\n"
        f"def launch(x: {annotation}):\n    x.create()\n"
    )
    # When migrating the annotated receiver.
    result = migrate(source)
    # Then proven wrappers preserve their established client semantics.
    assert result.source == source.replace("x.create()", "x.v0.create()")
    assert result.items == ()
