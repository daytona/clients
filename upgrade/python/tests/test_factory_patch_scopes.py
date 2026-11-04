# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Factory patch obligations belong only to scopes reaching changed consumers."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "installation",
    [
        "@patch('provider.make')\ndef test_case(factory):\n    {action}\n",
        "def test_case():\n    with patch('provider.make'):\n        {action}\n",
        "def test_case():\n    with patch.object(provider, 'make'):\n        {action}\n",
        "def test_case():\n    patcher = patch('provider.make')\n    factory = patcher.start()\n    {action}\n    patcher.stop()\n",
        "def test_case(mocker):\n    mocker.patch('provider.make')\n    {action}\n",
        "def test_case(monkeypatch):\n    monkeypatch.setattr(provider, 'make', lambda: None)\n    {action}\n",
    ],
)
@pytest.mark.parametrize(
    "action",
    [
        "pass",
        "provider.make().close()",
        "provider.unchanged()",
        "provider.consume()",
        "relay.run()",
        "provider.make().create()",
    ],
)
def test_reports_factory_patch_when_scope_reaches_consumer(
    tmp_path: Path, installation: str, action: str
) -> None:
    # Given a migrated factory consumer and an independently scoped patch.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\ndef unchanged(): return make().close()\n",
        "relay": "import provider\ndef run(): return provider.consume()\n",
        "test_app": "from unittest.mock import patch\nimport provider\nimport relay\n"
        + installation.format(action=action),
    }
    # When migration inspects the patch's own scope.
    results = migrate_project(sources, tmp_path)
    # Then only a reachable consumer creates an obligation.
    assert "make().v0.create()" in results["provider"].source
    assert [item["kind"] for item in results["test_app"].items] == (
        []
        if action in {"pass", "provider.make().close()", "provider.unchanged()"}
        else ["mock-target"]
    )


def test_ignores_factory_patch_when_consumer_is_outside_with(tmp_path: Path) -> None:
    # Given a consumer called only after the patch's lifetime ends.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n",
        "app": "from unittest.mock import patch\nimport provider\ndef test_case():\n"
        "    with patch('provider.make'):\n        pass\n    provider.consume()\n",
    }
    # When the consumer migrates outside the with block.
    results = migrate_project(sources, tmp_path)
    # Then the expired patch needs no adaptation.
    assert not results["app"].items


def test_reports_only_patch_whose_decorated_test_calls_consumer(tmp_path: Path) -> None:
    # Given two patches of the same factory in independent tests.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n",
        "test_app": "from unittest.mock import patch\nimport provider\n"
        "@patch('provider.make')\ndef test_unused(factory):\n    pass\n"
        "@patch('provider.make')\ndef test_used(factory):\n    provider.consume()\n",
    }
    # When only one test reaches the migrated consumer.
    results = migrate_project(sources, tmp_path)
    # Then only that test's patch needs manual review.
    assert [(item["kind"], item["line"]) for item in results["test_app"].items] == [
        ("mock-target", 6)
    ]


@pytest.mark.parametrize(
    "setup",
    [
        "patcher = patch('provider.make')\n",
        "def install():\n    return patch('provider.make')\n",
    ],
)
def test_keeps_factory_patch_review_when_scope_is_unknown(
    tmp_path: Path, setup: str
) -> None:
    # Given a module-level patch whose lifetime is controlled elsewhere.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n",
        "app": "from unittest.mock import patch\n" + setup,
    }
    # When no bounded scope can prove that the patch is unused.
    results = migrate_project(sources, tmp_path)
    # Then conservative review remains mandatory.
    assert [item["kind"] for item in results["app"].items] == ["mock-target"]
