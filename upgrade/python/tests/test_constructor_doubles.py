# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""New holder proofs must not silently change the contract of injected doubles."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "definition,call",
    [
        (
            "class Service:\n    def __init__(self, client: Optional[Daytona] = None): self.client = client or Daytona()\n",
            "Service(fake)",
        ),
        (
            "class Service:\n    def __init__(self, client: Optional[Daytona] = None): self.client = client if client is not None else Daytona()\n",
            "Service(client=fake)",
        ),
        (
            "from attrs import define, field\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
            "Service(fake)",
        ),
        (
            "import attr\n@attr.s(auto_attribs=True)\nclass Service:\n    client: Daytona = attr.ib(factory=Daytona)\n",
            "Service(client=fake)",
        ),
        (
            "from attrs import define, field\n@define(kw_only=True)\nclass Service:\n    label: str = 'label'\n    client: Daytona = field(factory=Daytona)\n",
            "Service(label='other', client=fake)",
        ),
        (
            "from pydantic import BaseModel, Field\nclass Service(BaseModel):\n    client: Daytona = Field(default_factory=Daytona)\n",
            "Service(client=fake)",
        ),
    ],
)
def test_reviews_fake_when_injected_into_newly_proven_holder(
    tmp_path: Path, definition: str, call: str
) -> None:
    # Given a constructor parameter that now feeds a migrated field access.
    sources = {
        "provider": "from typing import Optional\nfrom daytona import Daytona\n"
        + definition
        + "    def run(self): return self.client.create()\n",
        "app": "from types import SimpleNamespace\nfrom provider import Service\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n    assert "
        + call
        + ".run() == 73\n",
    }
    # When the holder's whole-object ownership is outside the adapter grammar.
    results = migrate_project(sources, tmp_path)
    # Then no speculative wrapper is inserted, and the changed contract is visible.
    assert "self.client.v0.create()" in results["provider"].source
    assert ast.dump(ast.parse(results["app"].source)) == ast.dump(
        ast.parse(sources["app"])
    )
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
