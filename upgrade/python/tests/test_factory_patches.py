# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unadapted factory mocks must be reported at installation, not silently broken."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project


@pytest.mark.parametrize(
    "installation",
    [
        "@patch('provider.make')\ndef test_case(factory):\n{setup}\n    assert provider.consume() == 73\n",
        "def test_case():\n    with patch('provider.make') as factory:\n{nested_setup}\n        assert provider.consume() == 73\n",
        "def test_case():\n    with patch.object(provider, 'make') as factory:\n{nested_setup}\n        assert provider.consume() == 73\n",
        "def test_case(mocker):\n    factory = mocker.patch('provider.make')\n{setup}\n    assert provider.consume() == 73\n",
    ],
)
@pytest.mark.parametrize(
    "setup",
    [
        "factory.return_value.create.return_value = 73",
        "factory.return_value = SimpleNamespace(create=lambda: 73)",
        "factory.side_effect = lambda: SimpleNamespace(create=lambda: 73)",
        "fake = factory.return_value\nfake.create.return_value = 73",
    ],
)
def test_reviews_patch_when_bound_factory_result_consumer_moves(
    tmp_path: Path, installation: str, setup: str
) -> None:
    # Given real decorator/context/mock-fixture patches, working before migration.
    app = (
        "from unittest.mock import patch\nfrom types import SimpleNamespace\nimport provider\n"
        + installation.format(
            setup="\n".join("    " + line for line in setup.splitlines()),
            nested_setup="\n".join("        " + line for line in setup.splitlines()),
        )
    )
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n",
        "test_app": app,
    }
    migrate_project({}, tmp_path)  # Supply only the network-free SDK fixture.
    for name, source in sources.items():
        (tmp_path / (name + ".py")).write_text(source)
    invoke = (
        "from contextlib import ExitStack\nfrom types import SimpleNamespace\n"
        "from unittest.mock import patch\nimport test_app as app\n"
        "with ExitStack() as stack:\n"
        "    app.test_case(SimpleNamespace(patch=lambda *a, **k: stack.enter_context(patch(*a, **k))))\n"
        if "def test_case(mocker)" in app
        else "import test_app as app; app.test_case()"
    )
    baseline = subprocess.run(
        [sys.executable, "-B", "-c", invoke],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stderr
    # When the factory result's production access moves, not its replacement setup.
    results = migrate_project(sources, tmp_path)
    # Then every installation carries an actionable obligation at the patch site.
    assert "make().v0.create()" in results["provider"].source
    patches = [
        item for item in results["test_app"].items if item["kind"] == "mock-target"
    ]
    assert len(patches) == 1
    assert "patch" in app.splitlines()[patches[0]["line"] - 1]
    migrated = subprocess.run(
        [sys.executable, "-B", "-c", invoke],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (
        migrated.returncode != 0
    )  # The explicit item, not a fabricated adaptation, guards this break.


@pytest.mark.parametrize(
    "replacement",
    [
        "patch('provider.make', side_effect=lambda: SimpleNamespace(create=lambda: 73))",
        "patch('provider.make', side_effect=[SimpleNamespace(create=lambda: 73)])",
        "patch('provider.make', new=lambda: SimpleNamespace(create=lambda: 73))",
        "patch.object(provider.Manager, 'make', side_effect=lambda: SimpleNamespace(create=lambda: 73))",
        "patch('provider.Manager.make', side_effect=lambda: SimpleNamespace(create=lambda: 73))",
    ],
)
def test_reviews_patch_when_factory_replacement_cannot_be_adapted(
    tmp_path: Path, replacement: str
) -> None:
    # Given an opaque replacement of a module factory or class method.
    method = "Manager" in replacement
    sources = {
        "provider": "from daytona import Daytona\n"
        + (
            "class Manager:\n    @staticmethod\n    def make() -> Daytona: return Daytona()\ndef consume(): return Manager.make().create()\n"
            if method
            else "def make() -> Daytona: return Daytona()\ndef consume(): return make().create()\n"
        ),
        "app": "from types import SimpleNamespace\nfrom unittest.mock import patch\nimport provider\n"
        f"def test_case():\n    with {replacement}:\n        assert provider.consume() == 73\n",
    }
    # When the consumer moves but the inline factory stays opaque to adaptation.
    results = migrate_project(sources, tmp_path)
    # Then either adaptation works at runtime or the patch itself has a review item.
    run = subprocess.run(
        [sys.executable, "-B", "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    patches = [item for item in results["app"].items if item["kind"] == "mock-target"]
    assert run.returncode == 0 or patches, run.stderr
    assert all(
        "with patch" in sources["app"].splitlines()[item["line"] - 1]
        for item in patches
    )


@pytest.mark.parametrize(
    "consumer",
    ["make().close()", "external(make())", "(make() if True else Daytona()).close()"],
)
def test_ignores_default_factory_patch_when_consumers_stay_unchanged(
    tmp_path: Path, consumer: str
) -> None:
    # Given a patched factory used only by unchanged root/opaque consumers.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        "def external(client): return client.create()\n"
        f"def consume(): return {consumer}\n",
        "app": "from unittest.mock import patch\nimport provider\ndef test_case():\n"
        "    with patch('provider.make') as factory:\n"
        "        factory.return_value.create.return_value = 73\n"
        "        factory.return_value.close.return_value = 73\n"
        "        assert provider.consume() == 73\n",
    }
    # When no result consumer is rewritten to the v0 namespace.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-B", "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the original test still works without a spurious factory-patch warning.
    assert run.returncode == 0, run.stderr
    assert not results["app"].items
