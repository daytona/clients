# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sync-class proofs survive only class-return paths that are still certain."""

from __future__ import annotations

import pytest
from test_synchronous_mock_specs import migrate


@pytest.mark.parametrize(
    "replacement",
    [
        "    @staticmethod\n    def select() -> type[AsyncDaytona]:\n        return AsyncDaytona\n",
        "    if condition:\n        @staticmethod\n        def select() -> type[AsyncDaytona]:\n            return AsyncDaytona\n",
        "    @property\n    def select(self) -> type[AsyncDaytona]:\n        return AsyncDaytona\n",
        "    select = unknown\n",
        "    class select: pass\n",
        "    from foreign import select\n",
    ],
)
def test_reviews_when_later_class_binding_replaces_sync_factory(
    replacement: str,
) -> None:
    # Given a sync factory overwritten by a definition or unclassified rebinding.
    source = (
        "from daytona import Daytona, AsyncDaytona\nfrom unittest.mock import MagicMock\n"
        "class Factory:\n    @staticmethod\n    def select() -> type[Daytona]:\n        return Daytona\n"
        + replacement
        + "client = MagicMock(spec=Factory.select())\nclient.create()\n"
    )

    # When the mock spec is resolved after the replacement.
    result = migrate(source)

    # Then the earlier sync marker cannot certify the new callable.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "annotation",
    [
        "Optional[type[Daytona]]",
        "type[Daytona] | None",
        "Union[type[Daytona], None]",
        "'Optional[type[Daytona]]'",
        "Annotated[Optional[type[Daytona]], 'metadata']",
    ],
)
@pytest.mark.parametrize("method", [False, True])
def test_reviews_when_factory_return_annotation_is_nullable(
    annotation: str, method: bool
) -> None:
    # Given a class-return factory whose annotation permits None.
    definition = (
        f"def select() -> {annotation}:\n    return Daytona if condition else None\n"
    )
    if method:
        definition = "class Factory:\n    @staticmethod\n" + "".join(
            "    " + line + "\n" for line in definition.splitlines()
        )
    source = (
        "from typing import Optional, Union, Annotated\n"
        "from daytona import Daytona\nfrom unittest.mock import MagicMock\n"
        + definition
        + f"client = MagicMock(spec={'Factory.select()' if method else 'select()'})\nclient.create()\n"
    )

    # When mock inference checks positive synchronous class provenance.
    result = migrate(source)

    # Then filtering away None cannot produce an unconditional class proof.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("owner", ["Other", "unresolved"])
@pytest.mark.parametrize(
    "mutation", ["OWNER.get = unknown", 'setattr(OWNER, "get", unknown)']
)
def test_invalidates_only_matching_or_unresolved_mutation_owners(
    owner: str, mutation: str
) -> None:
    # Given same-named members on unrelated known classes, or an unresolved owner.
    source = (
        "from daytona import Daytona\nfrom unittest.mock import MagicMock\n"
        "class Other: pass\n"
        "class Factory:\n    @staticmethod\n    def get() -> type[Daytona]:\n        return Daytona\n"
        + mutation.replace("OWNER", owner)
        + "\nclient = MagicMock(spec=Factory.get())\nclient.create()\n"
    )

    # When class-wide stores are joined before the mock spec is read.
    result = migrate(source)

    # Then a known different owner preserves proof; an unresolved one fences it.
    if owner == "Other":
        assert result.source == source.replace("client.create()", "client.v0.create()")
        assert result.items == ()
    else:
        assert result.source == source
        assert [item["kind"] for item in result.items] == ["mock-target"]
