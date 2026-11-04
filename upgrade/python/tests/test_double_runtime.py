# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Execute migrated doubles through migrated consumers, rather than pinning text."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate_project(sources: dict[str, str], root: Path) -> dict[str, SourceResult]:
    mapping = load_mapping()
    index = build_index(sources, mapping)
    results = {
        name: transform_source(
            source, TransformOptions(mapping), SourceContext(name + ".py", name, index)
        )
        for name, source in sources.items()
    }
    for name, result in results.items():
        (root / (name + ".py")).write_text(result.source)
    (root / "daytona.py").write_text(
        "from types import SimpleNamespace\n"
        "class Daytona:\n    def __init__(self):\n"
        "        self.v0 = SimpleNamespace(create=lambda: 17, get=lambda: 17)\n"
    )
    return results


@pytest.mark.parametrize(
    "factory",
    ["SimpleNamespace()", 'types.ModuleType("fake")', "Mock()", "MagicMock()"],
)
def test_fake_module_runs_when_non_autovivifying_factory_is_migrated(
    tmp_path: Path, factory: str
) -> None:
    # Given a valid 0.x fake; only Mock factories auto-create missing attributes.
    sources = {
        "provider": "def load():\n    import daytona\n    return daytona\n"
        "def consume(): return load().common.errors.DaytonaError\n",
        "app": "import types\nfrom types import SimpleNamespace\n"
        "from unittest.mock import Mock, MagicMock, patch\nimport provider\n"
        f"def test_case():\n    fake = {factory}\n"
        "    fake.common = SimpleNamespace(errors=SimpleNamespace(DaytonaError=ValueError))\n"
        "    with patch('provider.load', return_value=fake):\n"
        "        assert provider.consume() is ValueError\n"
        "    assert fake.common.errors.DaytonaError is ValueError\n",
    }
    # When migrating and running through the real consumer surface.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then identity and the error-classification result survive.
    assert run.returncode == 0, run.stderr
    assert not results["app"].items


@pytest.mark.parametrize(
    "factory", ["Fake()", "SimpleNamespace(create=Mock(return_value=42))"]
)
@pytest.mark.parametrize("consumer", ["consume", "make_service().consume"])
def test_client_double_runs_when_passed_to_proven_client_parameter(
    tmp_path: Path, factory: str, consumer: str
) -> None:
    # Given a local fake whose observable call history must keep its identity.
    sources = {
        "provider": "from daytona import Daytona\ndef consume(client: Daytona): return client.create()\n"
        "class Service:\n    def consume(self, client: Daytona): return client.create()\n"
        "def make_service(): return Service()\n",
        "app": "from types import SimpleNamespace\nfrom unittest.mock import Mock\n"
        "from provider import consume, make_service\nclass Fake:\n"
        "    def __init__(self): self.calls = []\n"
        "    def create(self):\n        self.calls.append(42)\n        return 42\n"
        f"def test_case():\n    fake = {factory}\n"
        f"    assert {consumer}(fake) == 42\n"
        + (
            "    assert fake.calls == [42]\n"
            if factory == "Fake()"
            else "    fake.create.assert_called_once()\n"
        ),
    }
    # When both ends of the injection are migrated.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the production rewrite and the original fake behavior both hold.
    assert "client.v0.create()" in results["provider"].source
    assert run.returncode == 0, run.stderr
    assert not results["app"].items


def test_nested_namespace_runs_when_mocked_manager_method_returns_client(
    tmp_path: Path,
) -> None:
    # Given Harbor's singleton patch and nested get_client return-value literal.
    sources = {
        "provider": "from daytona import Daytona\nclass Manager:\n"
        "    @classmethod\n    async def instance(cls) -> 'Manager': return cls()\n"
        "    async def get_client(self) -> Daytona: return Daytona()\n"
        "async def consume():\n    manager = await Manager.instance()\n"
        "    client = await manager.get_client()\n    return await client.get()\n",
        "app": "import asyncio\nfrom types import SimpleNamespace\n"
        "from unittest.mock import AsyncMock, patch\nfrom provider import Manager, consume\n"
        "def test_case():\n    manager = SimpleNamespace(get_client=AsyncMock(\n"
        "        return_value=SimpleNamespace(get=AsyncMock(return_value=42))))\n"
        "    with patch.object(Manager, 'instance', AsyncMock(return_value=manager)):\n"
        "        assert asyncio.run(consume()) == 42\n",
    }
    # When indexed return contracts propagate through the literal replacement graph.
    results = migrate_project(sources, tmp_path)
    assert "client.v0.get(" in results["provider"].source
    assert results["provider"].items == ()
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the nested client is shaped correctly without wrapping the manager/sandbox.
    assert run.returncode == 0, run.stderr
    assert not results["app"].items
