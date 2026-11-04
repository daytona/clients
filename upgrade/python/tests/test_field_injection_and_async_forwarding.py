# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Non-constructor field injection and async runners retain manual mock links."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "definition,call",
    [
        (
            "from pydantic import BaseModel, ConfigDict, Field\nclass Service(BaseModel):\n    model_config = ConfigDict(arbitrary_types_allowed=True)\n    client: Daytona = Field(default_factory=Daytona)\n",
            "Service.model_construct(client=fake)",
        ),
        (
            "from pydantic import BaseModel, ConfigDict, Field\nclass Service(BaseModel):\n    model_config = ConfigDict(arbitrary_types_allowed=True)\n    client: Daytona = Field(default_factory=Daytona)\n",
            "Service().model_copy(update={'client': fake})",
        ),
        (
            "from attrs import define, field, evolve\n@define\nclass Service:\n    client: Daytona = field(factory=Daytona)\n",
            "evolve(Service(), client=fake)",
        ),
        (
            "import attrs\n@attrs.define\nclass Service:\n    client: Daytona = attrs.field(factory=Daytona)\n",
            "attrs.evolve(Service(), client=fake)",
        ),
        (
            "from dataclasses import dataclass, field, replace\n@dataclass\nclass Service:\n    client: Daytona = field(default_factory=Daytona)\n",
            "replace(Service(), client=fake)",
        ),
        (
            "import dataclasses\n@dataclasses.dataclass\nclass Service:\n    client: Daytona = dataclasses.field(default_factory=Daytona)\n",
            "dataclasses.replace(Service(), client=fake)",
        ),
    ],
)
def test_reviews_fake_when_helper_injects_proven_client_field(
    tmp_path: Path, definition: str, call: str
) -> None:
    # Given a supported holder with an alternate injection path into its client field.
    source = (
        "from daytona import Daytona\nfrom types import SimpleNamespace\n"
        + definition
        + "    def run(self): return self.client.create()\n"
        "def test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        f"    assert {call}.run() == 73\n"
    )
    # When the field consumer migrates but ownership cannot justify reshaping the fake.
    result = migrate_project({"app": source}, tmp_path)["app"]
    # Then the injection site is linked to the changed contract, without wrapping it.
    assert "self.client.v0.create()" in result.source
    assert "SimpleNamespace(v0=" not in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)
    if "dataclasses" in definition:
        # Resolving the reported obligation by adding v0 restores the real dataclass path.
        (tmp_path / "app.py").write_text(
            result.source.replace("    assert ", "    fake.v0 = fake\n    assert ")
        )
        run = subprocess.run(
            [sys.executable, "-c", "import app; app.test_case()"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, run.stderr


@pytest.mark.parametrize(
    "runner",
    [
        "anyio.run(consume, fake)",
        "trio.run(consume, fake)",
        "asyncio.run(consume(fake))",
    ],
)
def test_reviews_fake_when_runner_forwards_to_changed_async_consumer(
    tmp_path: Path, runner: str
) -> None:
    # Given a fake passed to a typed async consumer through a framework runner.
    source = (
        "import anyio, trio, asyncio\nfrom daytona import AsyncDaytona\n"
        "from types import SimpleNamespace\nfrom unittest.mock import AsyncMock\n"
        "async def consume(client: AsyncDaytona): return await client.create()\n"
        "def test_case():\n    fake = SimpleNamespace(create=AsyncMock(return_value=73))\n"
        f"    assert {runner} == 73\n"
    )
    # When runner arguments identify a changed consumer but not exclusive ownership.
    result = migrate_project({"app": source}, tmp_path)["app"]
    # Then the unchanged fake gets a review obligation instead of silent acceptance.
    assert "client.v0.create()" in result.source
    assert "SimpleNamespace(v0=" not in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)


def test_ignores_same_named_helpers_when_no_client_field_or_changed_consumer(
    tmp_path: Path,
) -> None:
    # Given unrelated field names and an async consumer that never touches v0.
    source = (
        "import anyio\nfrom dataclasses import dataclass, replace\nfrom daytona import Daytona\n"
        "from types import SimpleNamespace\n@dataclass\nclass Service:\n    client: str = ''\n"
        "async def consume(client: Daytona): return 73\n"
        "def test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        "    replace(Service(), client=fake)\n    anyio.run(consume, fake)\n"
    )
    # When helper spelling alone supplies no client-consumer proof.
    result = migrate_project({"app": source}, tmp_path)["app"]
    # Then unrelated code and diagnostics stay unchanged.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert result.items == ()
