# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Default-factory field proofs belong to instances, never class attributes."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize("factory", ["Daytona", "AsyncDaytona"])
def test_instance_field_moves_when_factory_is_root_client(factory: str) -> None:
    # Given an unwritten field initialized by the SDK constructor.
    source = f"from dataclasses import dataclass, field\nfrom daytona import {factory}\n@dataclass\nclass Worker:\n    client: {factory} = field(default_factory={factory})\nWorker().client.create()\n"
    # When looking up that field on a direct default-constructed instance.
    result = migrate(source)
    # Then its client member is proven, without a manual item.
    assert result.source == source.replace(
        "Worker().client.create()", "Worker().client.v0.create()"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "write",
    [
        "self.client = Other()",
        "del self.client",
        "Worker.client = Other()",
        "setattr(self, key, Other())",
    ],
)
def test_instance_field_stays_manual_when_another_write_can_replace_it(
    write: str,
) -> None:
    # Given a default factory plus a potentially conflicting write.
    source = f"from dataclasses import dataclass, field\nfrom daytona import Daytona\n@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n    def reset(self):\n        {write}\nWorker().client.create()\n"
    # When joining the class-wide writes.
    result = migrate(source)
    # Then no field lookup receives a guessed namespace.
    assert "\nWorker().client.v0" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "receiver", ["Worker", "Worker(Other())", "Worker(client=Other())"]
)
def test_factory_does_not_prove_when_lookup_is_not_default_instance(
    receiver: str,
) -> None:
    # Given a class lookup or a constructor that overrides the field's default.
    source = f"from dataclasses import dataclass, field\nfrom daytona import Daytona\n@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n{receiver}.client.create()\n"
    # When inspecting the non-default lookup.
    result = migrate(source)
    # Then a default factory does not prove this receiver.
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize(
    "initializer",
    ["def __init__(self): pass", "def __new__(cls): return Other()"],
)
def test_factory_stays_unproven_when_constructor_is_user_owned(
    initializer: str,
) -> None:
    # Given a dataclass whose constructor does not guarantee a factory call.
    source = f"from dataclasses import dataclass, field\nfrom daytona import Daytona\n@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n    {initializer}\nWorker().client.create()\n"
    # When inspecting the field on a default constructor call.
    result = migrate(source)
    # Then the field is not assumed to have been initialized by Daytona.
    assert ".v0.create()" not in result.source
