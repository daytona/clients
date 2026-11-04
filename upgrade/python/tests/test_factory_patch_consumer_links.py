# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Factory patch review follows result use, not factory names or return annotations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "consumer",
    [
        "def consume():\n    client = make()\n    return client.create()\n",
        "def consume():\n    client = make()\n    external(client)\n    return client.create()\n",
        "def typed(client: Daytona): return client.create()\ndef consume(): return typed(make())\n",
        "def relay() -> Daytona: return make()\ndef consume(): return relay().create()\n",
        "def consume(): return getattr(make(), 'create')()\n",
        "def consume(): return (make() or Daytona()).create()\n",
    ],
)
def test_reviews_patch_when_factory_result_reaches_namespace_consumer(
    tmp_path: Path, consumer: str
) -> None:
    # Given aliases, forwarding, lookups, branches, or mixed consumers of one factory.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        "def external(client): return client.create()\n" + consumer,
        "app": "from unittest.mock import patch\nimport provider\ndef test_case():\n"
        "    with patch('provider.make') as factory:\n"
        "        factory.return_value.create.return_value = 73\n"
        "        assert provider.consume() == 73\n",
    }
    # When a result use changes, even if another use makes automatic adaptation unsafe.
    result = migrate_project(sources, tmp_path)["app"]
    # Then installation review cannot disappear with the replacement syntax.
    assert [item["kind"] for item in result.items] == ["mock-target"]
    assert result.items[0]["line"] == 4


@pytest.mark.parametrize("target", ["'provider.make'", "provider, 'make'"])
def test_reviews_monkeypatch_when_opaque_factory_replacement_reaches_namespace(
    tmp_path: Path, target: str
) -> None:
    # Given a real pytest monkeypatch whose named replacement returns an unowned fake.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n",
        "test_app": "from types import SimpleNamespace\nimport provider\n"
        "def replacement(): return SimpleNamespace(create=lambda: 73)\n"
        f"def test_case(monkeypatch):\n    monkeypatch.setattr({target}, replacement)\n"
        "    assert provider.consume() == 73\n",
    }
    migrate_project({}, tmp_path)
    for name, source in sources.items():
        (tmp_path / (name + ".py")).write_text(source)
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "test_app.py",
    ]
    baseline = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    # When the production result consumer moves but the named replacement does not.
    results = migrate_project(sources, tmp_path)
    migrated = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, check=False
    )
    # Then the real break has a manual item precisely at the installation site.
    assert migrated.returncode == 1, migrated.stdout + migrated.stderr
    assert [
        item["line"]
        for item in results["test_app"].items
        if item["kind"] == "mock-target"
    ] == [5]


def test_reviews_patch_when_target_is_known_but_options_are_unpacked(
    tmp_path: Path,
) -> None:
    # Given a resolvable target; unpacked options cannot authorize replacement adaptation.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n",
        "app": "from unittest.mock import patch\nimport provider\ndef test_case(options):\n"
        "    with patch('provider.make', **options) as factory:\n"
        "        factory.return_value.create.return_value = 73\n",
    }
    # When resolution of the target is independent of replacement options.
    result = migrate_project(sources, tmp_path)["app"]
    # Then an opaque replacement still gets the factory obligation.
    assert any(
        item["kind"] == "mock-target" and item["line"] == 4 for item in result.items
    )
