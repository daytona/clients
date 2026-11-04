# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Keyword receivers retain review links for injected client fields."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "definition,call",
    [
        (
            "import attrs\n@attrs.define\nclass Service:\n    client: Daytona = attrs.field(factory=Daytona)\n",
            "attrs.evolve(inst=service, client=fake)",
        ),
        (
            "from attrs import define, field, evolve\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
            "evolve(inst=service, client=fake)",
        ),
        (
            "import attr\n@attr.s\nclass Service:\n    client: Daytona = attr.ib(factory=Daytona)\n",
            "attr.evolve(inst=service, client=fake)",
        ),
        (
            "import dataclasses\n@dataclasses.dataclass\nclass Service:\n    client: Daytona = dataclasses.field(default_factory=Daytona)\n",
            "dataclasses.replace(obj=service, client=fake)",
        ),
        (
            "from dataclasses import dataclass, field, replace\n@dataclass\nclass Service:\n    client: Daytona = field(default_factory=Daytona)\n",
            "replace(obj=service, client=fake)",
        ),
    ],
)
def test_reviews_fake_when_helper_receiver_is_keyword_bound(
    tmp_path: Path, definition: str, call: str
) -> None:
    # Given a typed service copied with a fake through a keyword receiver.
    source = (
        "from daytona import Daytona\nfrom types import SimpleNamespace\n"
        + definition
        + "    def run(self): return self.client.create()\n"
        "def test_case():\n    service = Service()\n"
        "    fake = SimpleNamespace(create=lambda: 73)\n"
        f"    assert {call}.run() == 73\n"
    )
    # When the consumer moves without authority to reshape the injected fake.
    result = migrate_project({"app": source}, tmp_path)["app"]
    # Then the incompatible injection is explicit, never a silent break.
    assert "self.client.v0.create()" in result.source
    assert "SimpleNamespace(v0=" not in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)
