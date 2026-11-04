# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dataclass factories retain both direct proofs and uncertain SDK provenance."""

from __future__ import annotations

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize(
    "factory", ["{client}", "lambda: {client}()", "lambda: {client}(config)"]
)
@pytest.mark.parametrize("options", ["", ", repr=False", ", compare=False, repr=False"])
@pytest.mark.parametrize(
    "access", ["self.client.create()", "Worker().client.create()", "w.client.create()"]
)
def test_field_moves_when_factory_is_direct_client(
    client: str, factory: str, options: str, access: str
) -> None:
    # Given a factory with optional dataclass presentation settings.
    declaration = factory.format(client=client)
    source = (
        f"from dataclasses import dataclass, field\nfrom daytona import {client}, DaytonaConfig\nconfig = DaytonaConfig()\n"
        f"@dataclass\nclass Worker:\n    client: {client} = field(default_factory={declaration}{options})\n"
        + (
            f"    def run(self):\n        {access}\n"
            if access.startswith("self.")
            else f"w = Worker()\n{access}\n"
        )
    )
    # When resolving direct constructor evidence.
    result = migrate(source)
    # Then the client is migrated without a manual item.
    assert result.source == source.replace(
        access, access.replace(".create()", ".v0.create()")
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "factory",
    [
        "lambda: choose(Daytona)",
        "lambda: Daytona() if flag else Other()",
        "lambda value: Daytona()",
        "lambda client=Other(): client",
    ],
)
def test_field_stays_manual_when_linked_factory_is_not_proven(factory: str) -> None:
    # Given an SDK-annotated field whose factory is not a zero-arg direct constructor.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        f"@dataclass\nclass Worker:\n    client: Daytona = field(default_factory={factory}, repr=False)\n"
        "    def run(self):\n        self.client.create()\n"
    )
    # When the factory proof fails.
    result = migrate(source)
    # Then the linked annotation still produces review instead of silence.
    assert "self.client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_field_stays_unchanged_when_factory_has_no_daytona_link() -> None:
    # Given a completely unrelated field factory in an SDK-using module.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Other = field(default_factory=lambda: Other(), repr=False)\n"
        "    def run(self):\n        self.client.create()\n"
    )
    # When inspecting its factory.
    result = migrate(source)
    # Then a generic field name alone is not provenance.
    assert result.source == source
    assert result.items == ()


def test_field_stays_manual_when_lambda_constructor_is_rebound_before_execution() -> (
    None
):
    # Given late-bound constructor lookup, unlike a directly captured factory.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=lambda: Daytona())\n"
        "    def run(self):\n        self.client.create()\n"
        "Daytona = Other\nWorker().client.create()\n"
    )
    # When checking the deferred factory's execution environment.
    result = migrate(source)
    # Then the historical SDK link warrants review but not a rewrite.
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]


def test_field_stays_manual_when_unannotated_factory_function_returns_client() -> None:
    # Given linked return provenance without a client field annotation.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "def make(): return Daytona()\n@dataclass\nclass Worker:\n"
        "    client: object = field(default_factory=make, repr=False)\n"
        "    def run(self):\n        self.client.create()\n"
    )
    # When the factory is outside the direct-constructor proof grammar.
    result = migrate(source)
    # Then linked factory return evidence is not silently dropped.
    assert "self.client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
