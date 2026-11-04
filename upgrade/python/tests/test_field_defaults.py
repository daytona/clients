# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Generated field factories use bounded initialization proofs, not names alone."""

from __future__ import annotations

import ast
import os
import subprocess

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize(
    "definition",
    [
        "from attrs import define, field\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
        "import attr\n@attr.s(auto_attribs=True)\nclass Service:\n    client: Daytona = attr.ib(factory=Daytona)\n",
        "import attrs\nfrom attrs import Factory\n@attrs.define\nclass Service:\n    client: Daytona = attrs.field(default=Factory(Daytona))\n",
        "from pydantic import BaseModel, Field, ConfigDict\nclass Service(BaseModel):\n    model_config = ConfigDict(arbitrary_types_allowed=True)\n    client: Daytona = Field(default_factory=Daytona)\n",
    ],
)
def test_routes_field_when_framework_initializes_direct_client_factory(
    definition: str,
) -> None:
    # Given an attrs/Pydantic factory with an explicit client contract.
    source = (
        "from daytona import Daytona\n"
        + definition
        + "    def run(self): return self.client.create()\n"
    )
    # When applying the same direct-factory proof as dataclasses.
    result = migrate(source)
    # Then the ordinary factory is migrated, with an optional real-wheel witness.
    assert "self.client.v0.create()" in result.source
    assert not result.items
    interpreter = os.environ.get("DAYTONA_UPGRADE_TEST_SDK_PYTHON")
    if interpreter is None:
        pytest.skip("set DAYTONA_UPGRADE_TEST_SDK_PYTHON to a bundled SDK interpreter")
    imports = sorted(
        {
            node.module.split(".")[0]
            for node in ast.walk(ast.parse(definition))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        | {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(definition))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
    )
    available = subprocess.run(
        [
            interpreter,
            "-I",
            "-c",
            "import importlib.util, sys\n"
            "sys.exit(0 if all(importlib.util.find_spec(name) is not None "
            f"for name in {imports!r}) else 77)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if available.returncode == 77:
        pytest.skip(f"SDK interpreter lacks optional snippet imports: {imports}")
    assert available.returncode == 0, available.stdout + available.stderr
    run = subprocess.run(
        [
            interpreter,
            "-I",
            "-c",
            result.source
            + "from unittest.mock import patch\n"
            + "with patch('daytona.v0._Daytona.create', return_value=73) as create:\n"
            + "    assert Service().run() == 73\n"
            + "    create.assert_called_once_with()\n",
        ],
        env={**os.environ, "DAYTONA_API_KEY": "offline-regression-key"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize(
    "definition",
    [
        "from attrs import define, field\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona, converter=convert)\n",
        "from attrs import define, field\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n    @client.validator\n    def check(self, attribute, value): self.client = foreign\n",
        "from attrs import define, field\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n    def __attrs_post_init__(self): self.client = foreign\n",
        "from pydantic import BaseModel, Field, field_validator\nclass Service(BaseModel):\n    client: Daytona = Field(default_factory=Daytona)\n    @field_validator('client')\n    def change(cls, value): return foreign\n",
        "from pydantic import BaseModel, Field\nclass Service(BaseModel):\n    client: Daytona = Field(default_factory=Daytona)\n    def model_post_init(self, context): self.client = foreign\n",
        "from attrs import define, field\n@define(init=False)\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
        "from foreign import field\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
        "from attrs import define, field\nfield = unknown\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
    ],
)
def test_reviews_field_when_initialization_can_replace_annotated_client(
    definition: str,
) -> None:
    # Given a factory whose initialization is no longer a certified direct store.
    source = (
        "from daytona import Daytona\n"
        + definition
        + "    def run(self): return self.client.create()\n"
    )
    # When field initialization cannot be proven.
    result = migrate(source)
    # Then preserve the call but retain its annotation's review evidence.
    assert "return self.client.create()" in result.source
    assert any(item["kind"] == "fixme" for item in result.items)
