# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Decorator rebinding and collection mutation invalidate receiver proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_flow import migrate

import daytona_upgrade
from daytona_upgrade.flow import SourceContext
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import ProjectIndex
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("receiver", ["Factory().make()", "Factory.make()"])
def test_method_is_unproven_when_decorator_is_rebound_later(receiver: str) -> None:
    # Given a class decorator which is custom at the method's definition point.
    source = (
        "from daytona import Daytona\nfrom functools import cache\n"
        "def adapt(fn): return lambda *args: Other()\n"
        "class Factory:\n    decorate = adapt\n    @decorate\n"
        "    def make(self) -> Daytona: return Daytona()\n    decorate = cache\n"
        f"client = {receiver}\nclient.create()\n"
    )
    # When resolving the factory consumer.
    result = migrate(source)
    # Then a later transparent assignment cannot authorize a rewrite.
    assert "client.v0.create()" not in result.source


@pytest.mark.parametrize(
    "mutation",
    ["add(Other())", "update([Other()])", "symmetric_difference_update([Other()])"],
)
def test_set_elements_are_unproven_when_set_mutates(mutation: str) -> None:
    # Given a homogeneous set changed through an alias.
    source = (
        "from daytona import Daytona\nclients = {Daytona()}\nalias = clients\n"
        f"alias.{mutation}\nfor client in clients: client.create()\n"
    )
    # When migrating the loop.
    result = migrate(source)
    # Then the new elements cannot inherit client proofs.
    assert ".v0.create()" not in result.source


def test_class_elements_are_unproven_when_augmented_alias_mutates() -> None:
    # Given a different method which mutates an alias of the instance list.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self): self.clients = [Daytona()]\n"
        "    def replace(self):\n        alias = self.clients\n        alias += [Other()]\n"
        "    def run(self): self.clients[0].create()\n"
    )
    # When joining all instance writes.
    result = migrate(source)
    # Then the separate writer invalidates homogeneous element proofs.
    assert ".v0.create()" not in result.source


def test_earlier_call_stays_proven_when_later_argument_mutates() -> None:
    # Given left-to-right call arguments with a mutation only in the second.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\n"
        "consume(clients[0].create(), clients.append(Other()))\n"
        "clients[0].create()\n"
    )
    # When recording expression-position proofs.
    result = migrate(source)
    # Then only the call evaluated before mutation is rewritten.
    assert result.source.count(".v0.create()") == 1
    assert "consume(clients[0].v0.create()," in result.source


@pytest.mark.parametrize("name", ["Process", "FileSystem"])
def test_receiver_type_is_reported_when_absent_from_pinned_exports(
    tmp_path: Path, name: str
) -> None:
    # Given a receiver type known to inference but absent from the bundle.
    raw = json.loads(
        Path(daytona_upgrade.__file__).with_name("mapping.json").read_text()
    )
    raw["v0Exports"] = {"py": ["Sandbox"]}
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(raw))
    # When migrating its import.
    result = transform_source(
        f"from daytona import {name}\n",
        TransformOptions(load_mapping(path)),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then inference's receiver vocabulary is not an export whitelist.
    assert [item["kind"] for item in result.items] == ["private-import"]


def test_deferred_call_is_unproven_when_augmented_alias_mutates() -> None:
    # Given a closure created before a shared alias is changed.
    source = (
        "from daytona import Daytona\nclients = [Daytona()]\nalias = clients\n"
        "def run(): clients[0].create()\nalias += [Other()]\nrun()\n"
    )
    # When migrating the deferred body.
    result = migrate(source)
    # Then augmented writes already reach ScopeWrites.visit before dispatch.
    assert ".v0.create()" not in result.source
