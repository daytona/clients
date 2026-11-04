# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor arguments take precedence over dataclass field defaults."""

from __future__ import annotations

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize("default", ["Daytona()", "field(default_factory=Daytona)"])
@pytest.mark.parametrize(
    "arguments",
    ["client=Other()", "Other()", "client=MagicMock()", "*args", "**kwargs"],
)
@pytest.mark.parametrize("bound", [False, True])
def test_field_stays_manual_when_constructor_can_replace_default(
    default: str, arguments: str, bound: bool
) -> None:
    # Given a typed field whose default need not be the actual instance value.
    access = "w.client.create()" if bound else f"Worker({arguments}).client.create()"
    binding = f"w = Worker({arguments})\n" if bound else ""
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "from unittest.mock import MagicMock\nclass Other: pass\n"
        f"@dataclass\nclass Worker:\n    client: Daytona = {default}\n"
        + binding
        + access
        + "\n"
    )
    # When migrating the instance-expression path.
    result = migrate(source)
    # Then the annotation links provenance but cannot prove the supplied value.
    assert access in result.source
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("default", ["Daytona()", "field(default_factory=Daytona)"])
@pytest.mark.parametrize(
    "arguments", ["", "label='job'", "'job'", "client=Daytona()", "'job', Daytona()"]
)
@pytest.mark.parametrize("bound", [False, True])
def test_field_moves_when_constructor_preserves_or_proves_value(
    default: str, arguments: str, bound: bool
) -> None:
    # Given a preceding unrelated field and a default or explicit proven client.
    access = "w.client.create()" if bound else f"Worker({arguments}).client.create()"
    binding = f"w = Worker({arguments})\n" if bound else ""
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        f"@dataclass\nclass Worker:\n    label: str = ''\n    client: Daytona = {default}\n"
        + binding
        + access
        + "\n"
    )
    # When resolving the field's own constructor argument position.
    result = migrate(source)
    # Then unrelated arguments do not discard the default's proof.
    assert result.source == source.replace(
        access, access.replace(".create()", ".v0.create()")
    )
    assert result.items == ()


def test_self_field_moves_when_instance_call_overrides_default() -> None:
    # Given an explicitly typed self field and an uncertain external construction.
    source = (
        "from dataclasses import dataclass\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona = Daytona()\n"
        "    def run(self):\n        self.client.create()\n"
        "Worker(client=Other()).client.create()\n"
    )
    # When migrating both self and instance expressions.
    result = migrate(source)
    # Then the method's existing typed-field policy remains unchanged.
    assert "self.client.v0.create()" in result.source
    assert "Worker(client=Other()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "header",
    [
        "label: ClassVar[str] = ''",
        "label: 'ClassVar[str]' = ''",
        "label: str = field(init=False, default='')",
    ],
)
def test_field_stays_manual_when_non_parameter_precedes_overridden_field(
    header: str,
) -> None:
    # Given a class-only or init-excluded declaration before the first parameter.
    source = (
        "from dataclasses import dataclass, field\nfrom typing import ClassVar\nfrom daytona import Daytona\n"
        f"@dataclass\nclass Worker:\n    {header}\n    client: Daytona = Daytona()\n"
        "Worker(Other()).client.create()\n"
    )
    # When locating the client argument by generated parameter order.
    result = migrate(source)
    # Then declarations absent from __init__ never shift the argument's position.
    assert "Worker(Other()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("default", ["Daytona()", "field(default_factory=Daytona)"])
def test_field_stays_manual_when_proven_argument_has_conflicting_field_write(
    default: str,
) -> None:
    # Given an explicit client argument that another method can replace.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        f"@dataclass\nclass Worker:\n    client: Daytona = {default}\n"
        "    def reset(self): self.client = Other()\n"
        "Worker(client=Daytona()).client.create()\n"
    )
    # When resolving constructor evidence alongside class-wide writes.
    result = migrate(source)
    # Then the argument does not outrank contradictory field stores.
    assert "Worker(client=Daytona()).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "arguments", ["client=Other()", "Other()", "*args", "**kwargs"]
)
def test_field_stays_manual_when_inherited_dataclass_default_is_overridden(
    arguments: str,
) -> None:
    # Given a dataclass whose generated positional signature may include base fields.
    source = (
        "from dataclasses import dataclass\nfrom daytona import Daytona\n"
        "@dataclass\nclass Base:\n    client: Daytona = Daytona()\n"
        "@dataclass\nclass Worker(Base): pass\n"
        f"Worker({arguments}).client.create()\n"
    )
    # When an explicit constructor argument can replace the default.
    result = migrate(source)
    # Then a base class cannot bypass constructor precedence.
    assert f"Worker({arguments}).client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
