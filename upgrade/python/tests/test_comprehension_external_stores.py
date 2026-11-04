# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""External comprehension stores fence receiver proofs and workflow membership."""

from __future__ import annotations

import pytest
from test_flow import migrate
from test_workflow_binding_lifetimes import analyzed

from daytona_upgrade.suggestions import workflow_methods


@pytest.mark.parametrize(
    "expression",
    [
        "[None for owner.box in values]",
        "{None for owner.box in values}",
        "{0: None for owner.box in values}",
        "(None for owner.box in values)",
        "[None for (local, owner.box) in values]",
        "[None for owner[0] in values]",
        "[None for owner.__dict__['box'] in values]",
        "[api.v0.stop(owner.box) for owner.box in values]",
    ],
)
def test_workflow_excludes_later_call_when_comprehension_mutates_owner(
    expression: str,
) -> None:
    # Given a produced sandbox followed by a possible external store.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\n"
        f"owner.box = api.v0.create()\n{expression}\napi.v0.stop(owner.box)\n"
    )
    producer = next(call for call in flow.calls if call.method == "create")
    # When collecting calls owned by the original sandbox generation.
    members = workflow_methods(producer, flow)
    # Then the possibly replaced sandbox is never attributed to the old producer.
    assert members == []


@pytest.mark.parametrize(
    "expression",
    [
        "[None for owner.client in values]",
        "[None for owner[0] in values]",
        "[None for owner.__dict__['client'] in values]",
    ],
)
def test_client_is_unproven_when_comprehension_mutates_external_target(
    expression: str,
) -> None:
    # Given a client attribute which a comprehension may overwrite.
    source = (
        "from daytona import Daytona\n"
        f"owner.client = Daytona()\n{expression}\nowner.client.create()\n"
    )
    # When migrating the later call through the shared binding engine.
    result = migrate(source)
    # Then the previous client identity no longer justifies an automatic rewrite.
    assert "owner.client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_workflow_keeps_later_call_when_comprehension_owner_is_local() -> None:
    # Given a comprehension-local owner hiding an outer sandbox's owner.
    flow = analyzed(
        "from daytona import Daytona\napi = Daytona()\n"
        "owner.box = api.v0.create()\n"
        "[None for owner in owners for owner.box in values]\n"
        "api.v0.stop(owner.box)\n"
    )
    producer = next(call for call in flow.calls if call.method == "create")
    # When collecting the outer sandbox's workflow.
    members = workflow_methods(producer, flow)
    # Then stores on the local owner do not replace the outer sandbox.
    assert members == ["api.v0.stop"]


@pytest.mark.parametrize("target", ["owner.box", "owner[0]", "owner.__dict__['box']"])
def test_client_proof_is_fenced_when_comprehension_target_uses_literal_iterable(
    target: str,
) -> None:
    # Given a concrete mutation rather than an unknown iterable.
    source = (
        "from daytona import Daytona\n"
        "owner.box = Daytona()\n"
        f"[None for {target} in [Other()]]\n"
        "owner.box.create()\n"
    )
    # When migrating the original attribute's later call.
    result = migrate(source)
    # Then an unrelated replacement never receives a Daytona namespace access.
    assert "owner.box.v0.create()" not in result.source
    assert "owner.box.create()" in result.source


@pytest.mark.parametrize(
    "target",
    [
        "self.client",
        "self.__dict__['client']",
        "alias.client",
        "alias.__dict__['client']",
    ],
)
def test_class_client_proof_is_fenced_when_method_comprehension_mutates_instance(
    target: str,
) -> None:
    # Given initialization in one method and an external target in another.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        self.client = Daytona()\n"
        "    def replace(self):\n        alias = self\n"
        f"        [None for {target} in [Other()]]\n"
        "    def run(self):\n        return self.client.create()\n"
    )
    # When the shared proof analysis joins all instance writes.
    result = migrate(source)
    # Then the initializer cannot overrule an uncertain comprehension replacement.
    assert "self.client.v0.create()" not in result.source
    assert "self.client.create()" in result.source


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("typed_owner", [False, True])
@pytest.mark.parametrize("target", ["alias.box", "alias.__dict__['box']", "alias[0]"])
def test_alias_target_fences_original_owner_when_comprehension_mutates_member(
    deferred: bool, typed_owner: bool, target: str
) -> None:
    # Given a second binding referring to an object with a proven client member.
    source = (
        "from daytona import Daytona\n"
        + ("class Owner:\n    pass\nowner = Owner()\n" if typed_owner else "")
        + "owner.box = Daytona()\nalias = owner\n"
        + ("def run():\n    return owner.box.create()\n" if deferred else "")
        + f"[None for {target} in [Other()]]\n"
        + ("run()\n" if deferred else "owner.box.create()\n")
    )
    # When the alias's external member store invalidates receiver identity.
    result = migrate(source)
    # Then deferred and sequential accesses through the original owner stay safe.
    assert "owner.box.v0.create()" not in result.source
    assert "owner.box.create()" in result.source


@pytest.mark.parametrize("target", ["self.client", "self.__dict__['client']"])
def test_class_proof_survives_when_comprehension_receiver_is_local(target: str) -> None:
    # Given a local iteration variable hiding the instance receiver spelling.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        self.client = Daytona()\n"
        "    def replace(self):\n"
        f"        [None for self in owners for {target} in values]\n"
        "    def run(self):\n        return self.client.create()\n"
    )
    # When class-wide writes inspect the comprehension's lexical target scope.
    result = migrate(source)
    # Then stores on a local iteration value do not taint the instance client.
    assert "self.client.v0.create()" in result.source


def test_class_default_proof_survives_when_comprehension_name_is_local() -> None:
    # Given a class default hidden only by a comprehension-local target name.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    client = Daytona()\n"
        "    [None for client in [Other()]]\n"
        "    def run(self):\n        return self.client.create()\n"
    )
    # When target visitation accounts for the comprehension's own lexical scope.
    result = migrate(source)
    # Then a local target does not become a contradictory class default write.
    assert "self.client.v0.create()" in result.source
