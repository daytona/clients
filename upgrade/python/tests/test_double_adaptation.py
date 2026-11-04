# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Execute double adaptations and preserve unsafe producers for manual review."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_double_runtime import migrate_project
from test_flow import migrate


@pytest.mark.parametrize("suite", ["pass", "'A placeholder.'", "'Doc'; pass"])
def test_empty_class_runs_when_its_namespace_identity_is_consumed(
    tmp_path: Path,
    suite: str,
) -> None:
    # Given a valid empty fake passed to a consumer observing identity only.
    sources = {
        "provider": "from daytona import Daytona\ndef consume(client: Daytona): return client.create()\n",
        "app": "from provider import consume\n"
        f"class Fake: {suite}\n"
        "def test_case():\n    fake = Fake()\n    fake.create = lambda: 42\n"
        "    assert consume(fake) == 42\n",
    }
    # When adding the self-property to a one-line suite.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then property insertion succeeds even without an original indented body.
    assert "def v0" in results["app"].source
    assert "client.v0.create()" in results["provider"].source
    assert results["app"].items == ()
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize(
    "layout",
    [
        "    def __init__(self): setattr(self, 'v0', 42)\n",
        "    def __init__(self): object.__setattr__(self, 'v0', 42)\n",
        "    def __init__(self): self.__dict__['v0'] = 42\n",
        "    __slots__ = ()\n",
        "    def __setattr__(self, name, value): object.__setattr__(self, name, value)\n",
        "    def __getattr__(self, name): return 42\n",
    ],
)
def test_reviews_class_when_namespace_property_would_conflict(
    tmp_path: Path,
    layout: str,
) -> None:
    # Given a class with a conflicting namespace write or dynamic layout.
    sources = {
        "provider": "from daytona import Daytona\ndef consume(client: Daytona): return client.create()\n",
        "app": "from provider import consume\nclass Fake:\n"
        + layout
        + "    def create(self): return 42\n"
        "def test_case():\n    fake = Fake()\n    assert fake.create() == 42\n    consume(fake)\n",
    }
    # When the producer cannot be adapted without changing its original semantics.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "from app import Fake; assert Fake().create() == 42"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the producer still runs and the unresolved consumer is explicitly reported.
    assert "def v0" not in results["app"].source
    assert any(item["kind"] == "mock-target" for item in results["app"].items)
    assert run.returncode == 0, run.stderr


def test_module_alias_runs_when_import_splitting_expands_semicolon_line(
    tmp_path: Path,
) -> None:
    # Given a module fake assignment following a splittable import on the same line.
    sources = {
        "provider": "def load():\n    import daytona\n    return daytona\n"
        "def consume(): return load().common.errors.DaytonaError\n",
        "app": "from types import SimpleNamespace\nfrom unittest.mock import patch\n"
        "import provider\ndef test_case():\n"
        "    from daytona import Daytona, Sandbox; fake = SimpleNamespace()\n"
        "    fake.common = SimpleNamespace(errors=SimpleNamespace(DaytonaError=ValueError))\n"
        "    with patch('provider.load', return_value=fake):\n"
        "        assert provider.consume() is ValueError\n",
    }
    # When import expansion and double adaptation share one statement line.
    results = migrate_project(sources, tmp_path)
    (tmp_path / "daytona").mkdir()
    (tmp_path / "daytona" / "__init__.py").write_text("class Daytona: pass\n")
    (tmp_path / "daytona" / "v0.py").write_text("class Sandbox: pass\n")
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the correct assignment gets its alias despite preceding inserted imports.
    assert "fake.v0 = fake" in results["app"].source
    assert results["app"].items == ()
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("keyword", ["name='client'", "unsafe=True"])
def test_return_value_double_runs_when_mock_has_extra_explicit_keywords(
    tmp_path: Path,
    keyword: str,
) -> None:
    # Given a client factory replaced by a Mock with an extra non-routing option.
    sources = {
        "provider": "from daytona import Daytona\ndef make() -> Daytona: return Daytona()\n"
        "def consume(): return make().create()\n",
        "app": "from types import SimpleNamespace\nfrom unittest.mock import Mock, patch\n"
        "import provider\ndef test_case():\n"
        "    fake = SimpleNamespace(create=lambda: 42)\n"
        f"    with patch('provider.make', Mock(return_value=fake, {keyword})):\n"
        "        assert provider.consume() == 42\n",
    }
    # When following the explicit return_value rather than keyword count/order.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-c", "import app; app.test_case()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then both producer shape and rewritten consumer agree at runtime.
    assert "make().v0.create()" in results["provider"].source
    assert results["app"].items == ()
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("options", ["side_effect=other", "**options"])
def test_reviews_mock_factory_when_return_value_is_ambiguous(options: str) -> None:
    # Given a return_value whose use can be overridden at runtime.
    source = (
        "from daytona import Daytona\nfrom types import SimpleNamespace\n"
        "from unittest.mock import Mock, patch\n"
        "def make() -> Daytona: return Daytona()\n"
        "def test_case():\n    fake = SimpleNamespace(create=lambda: 42)\n"
        f"    patch('app.make', Mock(return_value=fake, {options}))\n"
    )
    # When no single return object can be proven.
    result = migrate(source)
    # Then adaptation is withheld and the existing mock-target item is emitted.
    assert "SimpleNamespace(v0=" not in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)


def test_one_line_suite_module_fake_is_reviewed_not_broken(tmp_path: Path) -> None:
    # Given a fake module created inside a one-line `if` suite.
    sources = {
        "provider": "import daytona\ndef load():\n    import daytona\n    return daytona\n"
        "def error_type():\n    return load().common.errors.DaytonaError\n",
        "test_app": "from types import SimpleNamespace\nimport provider\n"
        "def test_case(monkeypatch):\n    if True: fake = SimpleNamespace()\n"
        "    fake.common = SimpleNamespace(errors=SimpleNamespace(DaytonaError=ValueError))\n"
        "    monkeypatch.setattr(provider, 'load', lambda: fake)\n",
    }
    # When migrating it.
    results = migrate_project(sources, tmp_path)
    # Then no `fake.v0` access is produced without its alias; the fake is reviewed instead.
    assert "fake.v0 = fake" not in results["test_app"].source
    assert "fake.v0.common" not in results["test_app"].source
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)


def test_exec_in_nested_scope_blocks_double_certification() -> None:
    # Given a class body that may rebind the double's name through exec.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "def consume(client: Daytona): return client.create()\n"
        "def test_case():\n    fake = MagicMock()\n    fake.create.return_value = 1\n"
        "    class Holder:\n        exec('fake = object()')\n        consume(fake)\n"
    )
    # When migrating it.
    result = migrate(source)
    # Then the outer mock's members are not rewritten through `.v0`.
    assert "fake.v0.create" not in result.source
