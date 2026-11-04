# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Generated constructor signatures retain changed-client review obligations."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "definition,call",
    [
        (
            "from attrs import define\n@define\nclass Service:\n    client: Daytona\n",
            "Service(fake)",
        ),
        (
            "import attr\n@attr.s(auto_attribs=True)\nclass Service:\n    client: Daytona\n",
            "Service(client=fake)",
        ),
        (
            "from pydantic import BaseModel\nclass Service(BaseModel):\n    client: Daytona\n",
            "Service(client=fake)",
        ),
        (
            "from attrs import define, field\n@define\nclass Service:\n    label: str = field(init=False, default='label')\n    client: Daytona = field(factory=Daytona)\n",
            "Service(fake)",
        ),
        (
            "from attrs import define, field\n@define\nclass Service:\n    label: str = field(kw_only=True, default='label')\n    client: Daytona = field(factory=Daytona)\n",
            "Service(fake)",
        ),
        (
            "from attrs import define, field\n@define\nclass Service:\n    label: str = field(init=False, default='label')\n    client: Daytona\n",
            "Service(fake)",
        ),
        (
            "from dataclasses import dataclass, field\n@dataclass\nclass Service:\n    label: str = field(init=False, default='label')\n    client: Daytona = field(default_factory=Daytona)\n",
            "Service(fake)",
        ),
        (
            "from dataclasses import dataclass, KW_ONLY\n@dataclass\nclass Service:\n    _: KW_ONLY\n    client: Daytona\n",
            "Service(client=fake)",
        ),
        (
            "from pydantic import BaseModel, Field\nclass Service(BaseModel):\n    label: str = Field(default='label', init=False, exclude=True)\n    client: Daytona\n",
            "Service(client=fake)",
        ),
        (
            "from attrs import define, field\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona, alias='connection')\n",
            "Service(connection=fake)",
        ),
    ],
)
def test_reviews_injection_when_generated_client_field_reaches_moved_access(
    tmp_path: Path, definition: str, call: str
) -> None:
    # Given an independently supplied fake and the framework's actual input slot.
    sources = {
        "provider": "from daytona import Daytona\n"
        + definition
        + "    def run(self): return self.client.create()\n",
        "app": "from types import SimpleNamespace\nfrom provider import Service\n"
        "def test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        f"    return {call}.run()\n",
    }
    # When a generated constructor supplies a field used by a migrated method.
    results = migrate_project(sources, tmp_path)
    # Then a changed or uncertain field never silently breaks the injected fake.
    assert "self.client.v0.create()" in results["provider"].source
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
    assert ast.dump(ast.parse(results["app"].source)) == ast.dump(
        ast.parse(sources["app"])
    )


@pytest.mark.parametrize(
    "field", ["field(init=False, factory=Daytona)", "field(factory=Daytona)"]
)
def test_preserves_unrelated_argument_when_non_init_field_takes_no_slot(
    tmp_path: Path, field: str
) -> None:
    # Given a fake supplied only to a label, not the client field.
    sources = {
        "provider": "from daytona import Daytona\nfrom attrs import define, field\n"
        "@define\nclass Service:\n"
        f"    client: Daytona = {field}\n    label: str = 'label'\n"
        "    def run(self): return self.client.create()\n",
        "app": "from types import SimpleNamespace\nfrom provider import Service\n"
        "fake = SimpleNamespace(create=lambda: 73)\nService(label=fake)\n",
    }
    # When analyzing constructor inputs by their real field names.
    results = migrate_project(sources, tmp_path)
    # Then the unrelated value gets no injection review or speculative wrapper.
    assert results["app"].source == sources["app"]
    assert not results["app"].items


@pytest.mark.parametrize(
    "label",
    [
        "field(default='label', kw_only=flag)",
        "field(default='label', init=flag)",
        "field(default='label', **options)",
    ],
)
def test_reviews_injection_when_preceding_field_has_uncertain_signature(
    tmp_path: Path, label: str
) -> None:
    # Given a client that could occupy either positional slot at runtime.
    sources = {
        "provider": "from daytona import Daytona\nfrom attrs import define, field\n"
        "@define\nclass Service:\n"
        f"    label: str = {label}\n"
        "    client: Daytona = field(factory=Daytona)\n"
        "    def run(self): return self.client.create()\n",
        "app": "from types import SimpleNamespace\nfrom provider import Service\n"
        "fake = SimpleNamespace(create=lambda: 73)\nService(fake).run()\n",
    }
    # When exact positional routing cannot be established.
    results = migrate_project(sources, tmp_path)
    # Then retain review evidence for the possible injection, never certify a slot.
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
