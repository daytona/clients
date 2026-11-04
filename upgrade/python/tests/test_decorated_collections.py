# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unknown decorators retain collection-shaped manual-review evidence."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body",
    [
        "yield from source()",
        "try:\n        yield from source()\n    finally:\n        pass",
    ],
)
def test_wrapper_requires_review_when_delegating_to_decorated_generator(
    body: str,
) -> None:
    # Given a decorated client generator delegated through an unannotated wrapper.
    source = (
        "from daytona import Daytona\n"
        "@opaque\ndef source():\n    yield Daytona()\n"
        f"def wrapper():\n    {body}\n"
        "for value in wrapper():\n    value.create()\n"
    )
    # When migrating the wrapper's consumer.
    migrated = migrate(source)
    # Then delegated elements retain their link without gaining rewrite proof.
    assert ast.dump(ast.parse(migrated.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in migrated.items] == ["fixme"]


@pytest.mark.parametrize(
    "annotation", ["Iterator[Daytona]", "Iterable[Daytona]", "list[Daytona]"]
)
def test_loop_requires_review_when_decorated_factory_annotates_client_elements(
    annotation: str,
) -> None:
    # Given element annotations beneath a decorator whose behavior is unknown.
    source = (
        "from typing import Iterator, Iterable\nfrom daytona import Daytona\n"
        f"@opaque\ndef source() -> {annotation}:\n    return unknown()\n"
        "for value in source():\n    value.create()\n"
    )
    # When migrating a loop over the factory's result.
    migrated = migrate(source)
    # Then annotations keep the collection shape but cannot prove its elements.
    assert ast.dump(ast.parse(migrated.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in migrated.items] == ["fixme"]
