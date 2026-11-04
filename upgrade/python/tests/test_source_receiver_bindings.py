# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Source migration respects receiver identity and lexical scope boundaries."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "statement",
    [
        'client.default_language = "javascript"',
        "del client.default_language",
        "client.default_language += suffix",
    ],
)
def test_member_moves_when_target_is_a_proven_client(statement: str) -> None:
    # Given a stable client used as a write/delete target.
    source = "from daytona import Daytona\nclient = Daytona()\n" + statement + "\n"
    # When migrating the member access, not just LOAD expressions.
    result = migrate(source)
    # Then the setting reaches the same v0 client as resource calls.
    assert result.source == source.replace(
        "client.default_language", "client.v0.default_language"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "statement",
    [
        "client.default_language = value",
        "del client.default_language",
        "client.default_language += value",
    ],
)
def test_member_stays_when_generic_target_has_no_daytona_link(statement: str) -> None:
    # Given a generic parameter with no linked Daytona provenance.
    source = "import daytona\ndef run(client):\n    " + statement + "\n"
    # When migrating its write/delete target.
    result = migrate(source)
    # Then neither a namespace insertion nor an unrelated manual item is made.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "def run(self):\n        client.create()",
        "run = lambda: client.create()",
    ],
)
def test_class_local_stays_invisible_when_body_is_deferred(body: str) -> None:
    # Given a class-local client hiding an unrelated global.
    source = (
        "from daytona import Daytona\nclient = Other()\nclass C:\n    client = Daytona()\n    "
        + body
        + "\n"
    )
    # When entering a deferred body, Python skips the class namespace.
    result = migrate(source)
    # Then the unrelated global is not redirected.
    assert "client.v0.create()" not in result.source
    assert result.source == source
    assert result.items == ()


def test_global_client_moves_when_class_local_is_unrelated() -> None:
    # Given the opposite shadowing: the global is the actual client.
    source = "from daytona import Daytona\nclient = Daytona()\nclass C:\n    client = Other()\n    def run(self):\n        client.create()\n"
    # When entering the method's lexical scope.
    result = migrate(source)
    # Then only the global binding supplies the proof.
    assert result.source == source.replace("client.create()", "client.v0.create()")


@pytest.mark.parametrize(
    "name",
    [
        "DEFAULT_V2_API_URL",
        "V0_API_URL",
        "DaytonaV2Client",
        "AsyncDaytonaV2Client",
        "DaytonaV0Client",
        "AsyncDaytonaV0Client",
        "v0",
        "v2",
    ],
)
def test_root_import_stays_when_name_is_exported_by_sdk2(name: str) -> None:
    # Given a partly migrated file using a 2.x root export.
    source = f"from daytona import {name}\n"
    # When rerunning the upgrade.
    result = migrate(source)
    # Then the import remains loadable from the root.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("factory", ["Daytona", "AsyncDaytona"])
def test_dataclass_client_moves_when_default_factory_is_proven(factory: str) -> None:
    # Given a dataclass field with a bound SDK constructor as its factory.
    source = f"from daytona import {factory}\nfrom dataclasses import dataclass as dc, field as fld\n@dc\nclass C:\n    client: {factory} = fld(default_factory={factory})\n    def run(self):\n        self.client.create()\n"
    # When joining the class's concrete writes.
    result = migrate(source)
    # Then the instance field is a proven client.
    assert result.source == source.replace(
        "self.client.create()", "self.client.v0.create()"
    )


@pytest.mark.parametrize(
    "call", ["self.client.create()", "C.client.create()", "C().client.create()"]
)
def test_class_client_moves_when_all_writes_are_clients(call: str) -> None:
    # Given a concrete class attribute with no contradictory setters.
    source = f"from daytona import Daytona\nclass C:\n    client = Daytona()\n    def run(self):\n        return {call}\n"
    # When computing class-wide proofs.
    result = migrate(source)
    # Then both instance and class lookup resolve to the concrete client.
    assert result.source == source.replace(
        call, call.replace(".create()", ".v0.create()")
    )


@pytest.mark.parametrize("declaration", ["Daytona()", "field(default_factory=Daytona)"])
@pytest.mark.parametrize(
    "write", ["self.client = Other()", "del self.client", "C.client = Other()"]
)
def test_class_client_stays_manual_when_any_write_is_uncertain(
    declaration: str, write: str
) -> None:
    # Given a factory or class default plus a contradictory setter.
    source = f"from daytona import Daytona\nfrom dataclasses import dataclass, field\n@dataclass\nclass C:\n    client: Daytona = {declaration}\n    def reset(self):\n        {write}\n    def run(self):\n        self.client.create()\nC.client.create()\n"
    # When joining all possible attribute writes.
    result = migrate(source)
    # Then neither access is silently redirected.
    assert ".v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)
