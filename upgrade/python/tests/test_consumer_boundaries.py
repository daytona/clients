# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Opaque or unchanged consumer uses never certify a double's namespace."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "extra",
    ["launch(client)", "eval('client.create()')", "client.close()"],
)
def test_reviews_double_when_parameter_has_an_unmigrated_use(
    tmp_path: Path, extra: str
) -> None:
    # Given a direct call that moves, followed by an incompatible consumer use.
    sources = {
        "provider": "from daytona import Daytona\ndef consume(client: Daytona):\n    value = client.create()\n    "
        + extra
        + "\n    return value\n",
        "test_app": "from types import SimpleNamespace\nfrom unittest.mock import Mock\nfrom provider import consume\ndef test_consume():\n    fake = SimpleNamespace(create=Mock(return_value=42))\n    assert consume(fake) == 42\n",
    }
    # When certifying the whole parameter, rather than merely its annotation.
    results = migrate_project(sources, tmp_path)
    # Then an unsafe wrapper is replaced by the existing manual-review obligation.
    assert ast.dump(ast.parse(results["test_app"].source)) == ast.dump(
        ast.parse(sources["test_app"])
    )
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)


def test_preserves_double_when_consumer_only_replaces_a_method(tmp_path: Path) -> None:
    # Given a method replacement that the migration itself leaves for manual review.
    sources = {
        "provider": "from daytona import Daytona\ndef consume(client: Daytona):\n    client.create = lambda: 42\n",
        "test_app": "from types import SimpleNamespace\nfrom provider import consume\ndef test_consume():\n    fake = SimpleNamespace(create=lambda: 17)\n    consume(fake)\n    assert fake.create() == 42\n",
    }
    # When there is no rewritten access authorizing a new fake shape.
    results = migrate_project(sources, tmp_path)
    # Then preserve the fake and the consumer's existing review item.
    assert results["test_app"].source == sources["test_app"]
    assert any(item["kind"] == "mock-target" for item in results["provider"].items)


@pytest.mark.parametrize(
    "body",
    [
        "    def consume(self, client: Daytona): return self.get(client)\n    def get(self, client: Daytona): return client.create()\n",
        "    def consume(self, client: Daytona):\n        assert callable(client.create)\n        forwarded, flag = self.forward(client)\n        return forwarded.create()\n    def forward(self, client: Daytona) -> tuple[Daytona, bool]: return client, False\n",
        "    def consume(self, client: Daytona = None):\n        if client is None: client = unknown()\n        return client.create()\n",
    ],
)
def test_adapts_double_when_forwarding_stays_on_proven_client_paths(
    tmp_path: Path, body: str
) -> None:
    # Given Harbor-style forwarding, typed return relays, or None-default replacement.
    sources = {
        "provider": "from daytona import Daytona\nclass Service:\n" + body,
        "test_app": "from types import SimpleNamespace\nfrom unittest.mock import Mock\nfrom provider import Service\ndef test_consume():\n    fake = SimpleNamespace(create=Mock(return_value=42))\n    assert Service().consume(fake) == 42\n    fake.create.assert_called_once()\n",
    }
    # When every observable client access uses the migrated namespace.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then both result and call history survive, without false review obligations.
    assert run.returncode == 0, run.stdout + run.stderr
    assert not results["test_app"].items
