# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor evidence requires actual parameter storage and ordinary hooks."""

from __future__ import annotations

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize("decorator", ["dataclass", "dataclass(init=False)"])
@pytest.mark.parametrize(
    "body",
    [
        "pass",
        "self.client = Other()",
        "self.client = transform(client)",
        "client = Other(); self.client = client",
    ],
)
def test_field_stays_manual_when_custom_initializer_does_not_forward(
    decorator: str, body: str
) -> None:
    # Given an unresolved default and a constructor that discards or changes input.
    source = (
        "from dataclasses import dataclass\nfrom daytona import Daytona\n"
        f"@{decorator}\nclass Worker:\n    client: Daytona = unknown\n"
        f"    def __init__(self, client): {body}\n"
        "Worker(client=Daytona()).client.create()\n"
    )
    # When using a proven client argument.
    result = migrate(source)
    # Then the input is not proof of the stored receiver.
    assert "Worker(client=Daytona()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("decorator", ["dataclass", "dataclass(init=False)"])
def test_field_moves_when_custom_initializer_directly_stores_parameter(
    decorator: str,
) -> None:
    # Given an initializer that directly stores the same-named field argument.
    source = (
        "from dataclasses import dataclass\nfrom daytona import Daytona\n"
        f"@{decorator}\nclass Worker:\n    client: Daytona\n"
        "    def __init__(self, client): self.client = client\n"
        "Worker(client=Daytona()).client.create()\n"
    )
    # When resolving the concrete constructor input.
    result = migrate(source)
    # Then the direct store proves that instance's receiver.
    assert "Worker(client=Daytona()).client.v0.create()" in result.source
    assert result.items == ()


def test_field_stays_manual_when_dataclass_disables_generated_initializer() -> None:
    # Given a disabled initializer that accepts no generated field stores.
    source = (
        "from dataclasses import dataclass\nfrom daytona import Daytona\n"
        "@dataclass(init=False)\nclass Worker:\n    client: Daytona = unknown\n"
        "Worker(client=Daytona()).client.create()\n"
    )
    # When inspecting source without assuming the supplied argument is stored.
    result = migrate(source)
    # Then the unresolved field needs review, not a namespace rewrite.
    assert "Worker(client=Daytona()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_field_stays_manual_when_direct_parameter_store_has_later_unknown_write() -> (
    None
):
    # Given a direct initializer store followed by an opaque reset in another method.
    source = (
        "from dataclasses import dataclass\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona\n"
        "    def __init__(self, client): self.client = client\n"
        "    def reset(self): self.client = unknown\n"
        "Worker(client=Daytona()).client.create()\n"
    )
    # When joining both stores, which infer the same unknown kind.
    result = migrate(source)
    # Then counting direct storage cannot hide an additional unproven write.
    assert "Worker(client=Daytona()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("hook", ["__setattr__", "__delattr__"])
@pytest.mark.parametrize("inherited", [False, True])
def test_field_stays_manual_when_store_hook_is_opaque(
    hook: str, inherited: bool
) -> None:
    # Given an assignment hook that need not preserve the typed client store.
    definition = f"    def {hook}(self, *args): pass\n"
    source = (
        "from daytona import Daytona\n"
        + ("class Base:\n" + definition if inherited else "")
        + ("class Worker(Base):\n" if inherited else "class Worker:\n")
        + "    def __init__(self): self.client = Daytona()\n"
        + ("" if inherited else definition)
        + "    def run(self): self.client.create()\nWorker().client.create()\n"
    )
    # When resolving both self and external instance reads.
    result = migrate(source)
    # Then neither syntactic write establishes an ordinary stored attribute.
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]


@pytest.mark.parametrize("hook", ["__setattr__", "__delattr__"])
@pytest.mark.parametrize("reflective", [False, True])
def test_field_stays_manual_when_store_hook_is_replaced(
    hook: str, reflective: bool
) -> None:
    # Given a dataclass whose ordinary assignment machinery is replaced via alias.
    patch = (
        f'setattr(Alias, "{hook}", replacement)'
        if reflective
        else f"Alias.{hook} = replacement"
    )
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n"
        "    def run(self): self.client.create()\nAlias = Worker\n"
        + patch
        + "\nWorker().client.create()\n"
    )
    # When joining class lifecycle mutations.
    result = migrate(source)
    # Then replacing a write hook fences the same proofs as replacing a read hook.
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]
