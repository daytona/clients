# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Typed relay contracts preserve executable doubles across namespace changes."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "body",
    [
        "def identity(c: Daytona) -> Daytona: return c\ndef consume(handle: Daytona): return identity(handle).create()\n",
        "def identity(c: Daytona) -> tuple[Daytona, bool]: return c, True\ndef consume(handle: Daytona):\n    value, flag = identity(handle)\n    return value.create()\n",
        "def leaf(handle: Daytona): return handle.create()\ndef consume(handle: Daytona): return leaf(*[handle])\n",
        "def leaf(handle: Daytona): return handle.create()\ndef consume(handle: Daytona): return leaf(**{'handle': handle})\n",
        "def identity(c: Daytona) -> Daytona: return c\ndef relay(c: Daytona) -> Daytona: return identity(c)\ndef consume(handle: Daytona): return relay(handle).create()\n",
        "def identity(c: Daytona) -> Daytona: return c\ndef consume(handle: Daytona):\n    value = identity(handle)\n    return value.create()\n",
        "fresh = False\ndef identity(c: Daytona) -> tuple[Daytona, bool]:\n    if fresh: return Daytona(), True\n    return c, False\ndef consume(handle: Daytona):\n    value, close = identity(handle)\n    try: return value.create()\n    finally:\n        if close: value.close()\n",
    ],
)
def test_fake_runs_when_typed_relay_reaches_rewritten_access(
    tmp_path: Path, body: str
) -> None:
    # Given a root-shaped fake, with a non-vacuous call assertion.
    sources = {
        "provider": "from __future__ import annotations\nfrom daytona import Daytona\n"
        + body,
        "app": "from types import SimpleNamespace\nfrom unittest.mock import Mock\nfrom provider import consume\ndef test_case():\n    fake = SimpleNamespace(create=Mock(return_value=73))\n    assert consume(fake) == 73\n    fake.create.assert_called_once_with()\n",
    }
    # When migrating both sides of the typed relay.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the result and history survive without unresolved adaptation.
    assert run.returncode == 0, run.stdout + run.stderr
    assert all(not result.items for result in results.values())


@pytest.mark.parametrize(
    "body",
    [
        "def identity(c: Daytona) -> Daytona: return c\ndef consume(handle: Daytona):\n    identity(handle)\n    return 73\n",
        "def identity(c: Daytona) -> Daytona: return Daytona()\ndef consume(handle: Daytona):\n    identity(handle)\n    return 73\n",
    ],
)
def test_preserves_fake_when_relay_result_has_no_moved_access(
    tmp_path: Path, body: str
) -> None:
    # Given a typed return whose result is discarded.
    sources = {
        "provider": "from daytona import Daytona\n" + body,
        "app": "from types import SimpleNamespace\nfrom provider import consume\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73)\n    assert consume(fake) == 73\n    assert fake.create() == 73\n",
    }
    # When an annotation alone cannot authorize changing the fake's shape.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the still-root-shaped use remains valid.
    assert run.returncode == 0, run.stderr
    assert results["app"].source == sources["app"]


@pytest.mark.parametrize(
    "body",
    [
        "def identity(c: Daytona) -> Daytona:\n    c.close()\n    return c\ndef consume(handle: Daytona): return identity(handle).create()\n",
        "def identity(c: Daytona) -> Daytona: return unknown(c)\ndef consume(handle: Daytona): return identity(handle).create()\n",
        "def identity(c: Daytona) -> Daytona: return c\ndef consume(handle: Daytona):\n    value = identity(handle)\n    value.close()\n    return value.create()\n",
        "def leaf(handle: Daytona): return handle.create()\ndef consume(handle: Daytona): return leaf(*unknown(handle))\n",
        "def identity(c: Daytona) -> tuple[Daytona, bool]: return c, True\ndef consume(handle: Daytona):\n    value, close = identity(handle)\n    try: return value.create()\n    finally:\n        if close: value.close()\n",
        "def identity(c: Daytona) -> tuple[Daytona, bool]: return c, False\ndef consume(handle: Daytona):\n    value, close = identity(handle)\n    close = True\n    try: return value.create()\n    finally:\n        if close: value.close()\n",
    ],
)
def test_reviews_fake_when_changed_relay_cannot_certify_all_uses(
    tmp_path: Path, body: str
) -> None:
    # Given a changed consumer with opaque identity or incompatible root accesses.
    sources = {
        "provider": "from daytona import Daytona\n" + body,
        "app": "from types import SimpleNamespace\nfrom provider import consume\ndef test_case():\n    fake = SimpleNamespace(create=lambda: 73, close=lambda: None)\n    assert consume(fake) == 73\n",
    }
    # When the relay cannot prove that wrapping preserves all observations.
    results = migrate_project(sources, tmp_path)
    # Then leave the fake intact but never silently drop the changed contract.
    assert ast.dump(ast.parse(results["app"].source)) == ast.dump(
        ast.parse(sources["app"])
    )
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
