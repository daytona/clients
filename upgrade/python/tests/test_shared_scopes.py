# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared writes invalidate owning scopes, including deferred wildcard readers."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize("declaration", ["global", "nonlocal"])
@pytest.mark.parametrize("definition", ["def", "async def"])
@pytest.mark.parametrize("write", ["client = Other()", "del client"])
def test_owner_stays_unproven_when_nested_function_writes_shared_binding(
    declaration: str, definition: str, write: str
) -> None:
    # Given a helper which can replace or delete the owning scope's client.
    body = (
        "client = Daytona()\n"
        f"{definition} reset():\n    {declaration} client\n    {write}\n"
        "reset()\nclient.create()\n"
    )
    if declaration == "nonlocal":
        body = "def run():\n" + "".join(
            "    " + line + "\n" for line in body.splitlines()
        )
    source = "from daytona import Daytona\n" + body
    # When restoring the owning environment after analyzing the definition.
    result = migrate(source)
    # Then the deferred mutation cannot leave the old client proof in its owner.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "reader",
    [
        "def read():\n    return client.create()",
        "async def read():\n    return client.create()",
        "read = lambda: client.create()",
        "class Worker:\n    def read(self):\n        return client.create()",
    ],
)
def test_deferred_client_stays_unproven_when_later_wildcard_replaces_any_name(
    reader: str,
) -> None:
    # Given a deferred reader preceding an import with unknown exported names.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        + reader
        + "\nfrom other import *\n"
    )
    # When determining which inherited bindings remain stable until invocation.
    result = migrate(source)
    # Then the wildcard invalidates the client, not merely a literal '*' binding.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_owner_moves_when_nested_shared_declaration_has_no_write() -> None:
    # Given a declaration-only helper which never changes the outer client.
    source = "from daytona import Daytona\nclient = Daytona()\ndef read():\n    global client\n    return client\nclient.create()\n"
    # When restoring the enclosing scope after the read-only helper definition.
    result = migrate(source)
    # Then a declaration alone cannot destroy a valid sequential proof.
    assert result.source == source.replace("client.create()", "client.v0.create()")


@pytest.mark.parametrize(
    "definition",
    [
        "def outer():\n    def reset():\n        global client\n        client = Other()\n    reset()",
        "class Worker:\n    def reset(self):\n        global client\n        client = Other()",
    ],
)
def test_owner_stays_unproven_when_shared_writer_has_an_intermediate_scope(
    definition: str,
) -> None:
    # Given a global mutation nested behind another deferred function or class.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        + definition
        + "\ninvoke()\nclient.create()\n"
    )
    # When restoring each intervening scope after its definition.
    result = migrate(source)
    # Then no intermediate snapshot revives the mutated global's proof.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_owner_moves_only_before_shared_writer_definition() -> None:
    # Given a client with one use before and one after its mutator definition.
    source = "from daytona import Daytona\nclient = Daytona()\nclient.create()\ndef reset():\n    global client\n    client = Other()\nreset()\nclient.get('id')\n"
    # When applying the deferred writer's kill at the definition boundary.
    result = migrate(source)
    # Then the earlier use retains its proof but the later use needs review.
    assert "client.v0.create()" in result.source
    assert "\nclient.get('id')\n" in result.source


@pytest.mark.parametrize(
    "body",
    [
        "from other import *\ndef run(client: C):\n    client.create()",
        "if condition:\n    from other import *\ndef run(client: C):\n    client.create()",
        "try:\n    from other import *\nfinally:\n    def run(client: C):\n        client.create()",
        "try:\n    raise ValueError\n    from other import *\nfinally:\n    def run(client: C):\n        client.create()",
    ],
)
def test_annotation_stays_unproven_when_wildcard_can_replace_type_only_alias(
    body: str,
) -> None:
    # Given an SDK alias proven only for annotations before an unknown wildcard.
    source = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from daytona import Daytona as C\n"
        + body
        + "\n"
    )
    # When resolving annotations after direct or exceptional wildcard paths.
    result = migrate(source)
    # Then the typing environment cannot revive a proof the wildcard invalidated.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "reader",
    [
        "def run():\n    client: C\n    client.create()",
        "async def run():\n    client: C\n    client.create()",
        "def run():\n    def read(client: C):\n        client.create()",
        "def run(client: C):\n    client.create()",
    ],
)
def test_deferred_annotation_stays_unproven_when_future_wildcard_replaces_alias(
    reader: str,
) -> None:
    # Given deferred annotations whose type-only alias can be replaced later.
    source = (
        "from __future__ import annotations\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from daytona import Daytona as C\n"
        + reader
        + "\nfrom other import *\n"
    )
    # When analyzing the deferred reader before that later wildcard executes.
    result = migrate(source)
    # Then neither saved annotations nor nested definitions revive the alias.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_immediate_annotation_moves_when_use_precedes_future_wildcard() -> None:
    # Given a deferred reader and a separate immediate use before the wildcard.
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from daytona import Daytona as C\ndef run():\n    client: C\n    client.create()\nbefore: C\nbefore.create()\nfrom other import *\n"
    # When discarding typing proofs inside the deferred reader's own scope.
    result = migrate(source)
    # Then restoring the owner retains its still-valid pre-wildcard annotation.
    assert "\nbefore.v0.create()\n" in result.source


def test_annotation_moves_when_type_alias_is_reimported_after_wildcard() -> None:
    # Given a fresh typing import after all earlier proofs were invalidated.
    source = "from other import *\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from daytona import Daytona as C\ndef run(client: C):\n    client.create()\n"
    # When resolving a new annotation with no future wildcard replacement.
    result = migrate(source)
    # Then the fresh proof permits migration rather than poisoning the whole file.
    assert result.source == source.replace("client.create()", "client.v0.create()")
