# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Factory annotations alone cannot authorize changing a patched return value."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "consumer",
    [
        "def consume(): return external_call(make())\n",
        "def consume():\n    client = make()\n    return external_call(client)\n",
        "def consume(): return typed_call(make())\n",
    ],
)
@pytest.mark.parametrize(
    "replacement",
    ["return_value=fake", "new=lambda: fake", "new=Mock(return_value=fake)"],
)
def test_preserves_fake_when_factory_result_reaches_unrewritten_code(
    tmp_path: Path, consumer: str, replacement: str
) -> None:
    # Given a working replacement whose result escapes to an unchanged consumer.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        "def external_call(client): return client.create()\n"
        "def typed_call(client: Daytona): return external_call(client)\n" + consumer,
        "app": "from types import SimpleNamespace\nfrom unittest.mock import Mock, patch\n"
        "import provider\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        f"    with patch('provider.make', {replacement}):\n"
        "        assert provider.consume() == 73\n",
    }
    # When migrating the factory and its patch together.
    results = migrate_project(sources, tmp_path)
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the original test still runs, with an explicit unresolved mock link.
    assert run.returncode == 0, run.stderr
    assert ast.dump(ast.parse(results["app"].source)) == ast.dump(
        ast.parse(sources["app"])
    )


@pytest.mark.parametrize(
    "consumer",
    [
        "def consume(): return make().create()\n",
        "def consume():\n    client = make()\n    return client.create()\n",
        "def typed_call(client: Daytona): return client.create()\ndef consume(): return typed_call(make())\n",
    ],
)
def test_adapts_fake_when_every_factory_result_use_moves(
    tmp_path: Path, consumer: str
) -> None:
    # Given a replacement consumed exclusively through rewritten accesses.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        + consumer,
        "app": "from types import SimpleNamespace\nfrom unittest.mock import patch\n"
        "import provider\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        "    with patch('provider.make', return_value=fake):\n"
        "        assert provider.consume() == 73\n",
    }
    # When both sides migrate.
    results = migrate_project(sources, tmp_path)
    assert ".v0.create()" in results["provider"].source
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "fake"
            for target in node.targets
        )
        and isinstance(node.value, ast.Call)
        and any(keyword.arg == "v0" for keyword in node.value.keywords)
        for node in ast.walk(ast.parse(results["app"].source))
    )
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the changed consumer and its adapted replacement agree at runtime.
    assert run.returncode == 0, run.stderr
    assert all(not result.items for result in results.values())


def test_reviews_factory_when_only_one_of_its_consumers_moves(tmp_path: Path) -> None:
    # Given a factory shared by migrated and unrewritten consumers.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        "def consume(): return make().create()\n"
        "def external_call(client): return client.create()\n"
        "def other(): return external_call(make())\n",
        "app": "from types import SimpleNamespace\nfrom unittest.mock import patch\n"
        "import provider\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        "    with patch('provider.make', return_value=fake):\n"
        "        assert provider.other() == 73\n",
    }
    # When one changed access is insufficient to authorize wrapping all results.
    results = migrate_project(sources, tmp_path)
    assert ".v0.create()" in results["provider"].source
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the unchanged consumer still works and adaptation requires review.
    assert run.returncode == 0, run.stderr


def test_preserves_fake_when_factory_itself_escapes(tmp_path: Path) -> None:
    # Given one changed result use but another result produced through a callback.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        "def consume(): return make().create()\n"
        "def external_call(factory): return factory().create()\n"
        "def other(): return external_call(make)\n",
        "app": "from types import SimpleNamespace\nfrom unittest.mock import patch\n"
        "import provider\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n"
        "    with patch('provider.make', return_value=fake):\n"
        "        assert provider.other() == 73\n",
    }
    # When the indirect result cannot be certified by the direct consumer.
    results = migrate_project(sources, tmp_path)
    assert ".v0.create()" in results["provider"].source
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the callback still works and the unresolved result is reported.
    assert run.returncode == 0, run.stderr
