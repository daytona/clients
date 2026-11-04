# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Attribute interception and replaced initialization invalidate field proofs."""

from __future__ import annotations

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize("hook", ["__getattribute__", "__getattr__"])
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize(
    "access", ["self.client.create()", "Worker().client.create()", "w.client.create()"]
)
def test_field_stays_manual_when_attribute_access_is_intercepted(
    hook: str, inherited: bool, access: str
) -> None:
    # Given a typed client store whose reads can be replaced by a project hook.
    base = (
        f"class Base:\n    def {hook}(self, name): return Other()\n"
        if inherited
        else ""
    )
    local = "" if inherited else f"    def {hook}(self, name): return Other()\n"
    source = (
        "from daytona import Daytona\n"
        + base
        + ("class Worker(Base):\n" if inherited else "class Worker:\n")
        + "    def __init__(self): self.client = Daytona()\n"
        + local
        + (
            f"    def run(self):\n        {access}\n"
            if access.startswith("self.")
            else f"w = Worker()\n{access}\n"
        )
    )
    # When joining the class's field evidence.
    result = migrate(source)
    # Then initialization cannot certify an intercepted read.
    assert access in result.source
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "patch",
    ["Worker.__init__ = replacement", 'setattr(Worker, "__init__", replacement)'],
)
@pytest.mark.parametrize(
    "access", ["self.client.create()", "Worker().client.create()", "w.client.create()"]
)
def test_field_stays_manual_when_initializer_is_replaced(
    patch: str, access: str
) -> None:
    # Given a generated initializer that can be replaced elsewhere in the module.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n"
        + (
            f"    def run(self):\n        {access}\n"
            if access.startswith("self.")
            else ""
        )
        + patch
        + "\n"
        + ("" if access.startswith("self.") else f"w = Worker()\n{access}\n")
    )
    # When considering class-wide initializer writes.
    result = migrate(source)
    # Then self and external instance reads both require review.
    assert access in result.source
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_self_field_moves_when_only_unrelated_class_initializer_is_replaced() -> None:
    # Given an unaffected typed self field next to a separately patched class.
    source = (
        "from daytona import Daytona\nclass Other: pass\nOther.__init__ = replacement\n"
        "class Worker:\n    def __init__(self): self.client = Daytona()\n"
        "    def run(self):\n        self.client.create()\n"
    )
    # When checking class-specific safety.
    result = migrate(source)
    # Then an unrelated replacement does not fence this class.
    assert result.source == source.replace(
        "self.client.create()", "self.client.v0.create()"
    )
    assert result.items == ()


@pytest.mark.parametrize("hook", ["__getattribute__", "__getattr__"])
def test_factory_field_stays_manual_when_default_instance_intercepts_reads(
    hook: str,
) -> None:
    # Given the reviewed default-factory instance with a custom lookup hook.
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n"
        f"    def {hook}(self, name): return Other()\n"
        "Worker().client.create()\n"
    )
    # When resolving its default instance field.
    result = migrate(source)
    # Then even a direct factory cannot certify the intercepted read.
    assert "Worker().client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "patch", ["Base.__init__ = replacement", 'setattr(Base, "__init__", replacement)']
)
def test_field_stays_manual_when_project_base_initializer_is_replaced(
    patch: str,
) -> None:
    # Given a project base with replaced initialization and a typed derived field.
    source = (
        "from daytona import Daytona\nclass Base: pass\n" + patch + "\n"
        "class Worker(Base):\n    client: Daytona = Daytona()\n"
        "    def run(self):\n        self.client.create()\n"
        "Worker().client.create()\n"
    )
    # When inheriting the base's opaque lifecycle metadata.
    result = migrate(source)
    # Then neither self nor direct instance fields are proven.
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]


@pytest.mark.parametrize("owner", ["Worker", "Alias"])
@pytest.mark.parametrize("hook", ["__init__", "__getattribute__", "__getattr__"])
@pytest.mark.parametrize("reflective", [False, True])
def test_field_stays_manual_when_class_hook_is_replaced_through_alias(
    owner: str, hook: str, reflective: bool
) -> None:
    # Given a client factory and an external hook replacement, possibly aliased.
    patch = (
        f'setattr({owner}, "{hook}", replacement)'
        if reflective
        else f"{owner}.{hook} = replacement"
    )
    source = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\n"
        "    def run(self):\n        self.client.create()\nAlias = Worker\n"
        + patch
        + "\nWorker().client.create()\n"
    )
    # When collecting the class's potential lifecycle mutations.
    result = migrate(source)
    # Then aliases and reflective spellings fence the same field proofs.
    assert ".v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]
