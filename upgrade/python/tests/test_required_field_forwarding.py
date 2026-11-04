# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Required field annotations are review evidence even without a default factory."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "definition,injection",
    [
        (
            "import dataclasses\n@dataclasses.dataclass\nclass Service:",
            "dataclasses.replace(Service(Daytona()), client=fake)",
        ),
        (
            "import attrs\n@attrs.define\nclass Service:",
            "attrs.evolve(Service(Daytona()), client=fake)",
        ),
        (
            "import attr\n@attr.s(auto_attribs=True)\nclass Service:",
            "attr.evolve(Service(Daytona()), client=fake)",
        ),
        (
            "from pydantic import BaseModel, ConfigDict\nclass Service(BaseModel):\n    model_config = ConfigDict(arbitrary_types_allowed=True)",
            "Service.model_construct(client=fake)",
        ),
        (
            "from pydantic import BaseModel, ConfigDict\nclass Service(BaseModel):\n    model_config = ConfigDict(arbitrary_types_allowed=True)",
            "Service(client=Daytona()).model_copy(update={'client': fake})",
        ),
    ],
)
def test_reviews_injection_when_client_field_is_required(
    tmp_path: Path, definition: str, injection: str
) -> None:
    # Given a required field without a default factory and a working injected fake.
    source = (
        "from daytona import Daytona\nfrom types import SimpleNamespace\n"
        + definition
        + "\n    client: Daytona\n    def run(self): return self.client.create()\n"
        "def test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        f"    assert {injection}.run() == 73\n"
    )
    # When its method moves but the framework injection cannot prove fake ownership.
    result = migrate_project({"app": source}, tmp_path)["app"]
    # Then the annotation links the injection to review without authorizing wrapping.
    assert "self.client.v0.create()" in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)
    assert "SimpleNamespace(v0=" not in result.source
    if "dataclasses" in definition:
        (tmp_path / "app.py").write_text(
            result.source.replace("    assert ", "    fake.v0 = fake\n    assert ")
        )
        run = subprocess.run(
            [sys.executable, "-B", "-c", "import app; app.test_case()"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 0, run.stderr


def test_ignores_injection_when_required_field_is_not_client(tmp_path: Path) -> None:
    # Given an unrelated required field with identical injection spelling.
    source = (
        "from daytona import Daytona\nfrom dataclasses import dataclass, replace\n"
        "@dataclass\nclass Service:\n    client: str\n"
        "def test_case(): return replace(Service('old'), client='new')\n"
    )
    # When the field annotation supplies no SDK evidence.
    result = migrate_project({"app": source}, tmp_path)["app"]
    # Then no mock warning is invented.
    assert not result.items
