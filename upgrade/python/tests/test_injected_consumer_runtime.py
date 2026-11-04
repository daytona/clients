# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Execute migrated consumers and pytest fixture injection at runtime."""

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
        "def consume(client: Daytona): return launch(client)\n",
        "def consume(client: Daytona): return 42\n",
    ],
)
def test_preserves_fake_when_consumer_never_reads_v0(
    tmp_path: Path, consumer: str
) -> None:
    # Given a working fake consumed through an annotation without a moved access.
    sources = {
        "provider": "from daytona import Daytona\ndef launch(client): return client.create()\n"
        + consumer,
        "test_app": "from types import SimpleNamespace\nfrom unittest.mock import Mock\nfrom provider import consume\ndef test_consume():\n    fake = SimpleNamespace(create=Mock(return_value=42))\n    assert consume(fake) == 42\n",
    }
    # When the tool migrates and the test runs through the real callable boundary.
    results = migrate_project(sources, tmp_path)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the original fake still works and is not unnecessarily adapted/reviewed.
    assert run.returncode == 0, run.stdout + run.stderr
    assert results["test_app"].source == sources["test_app"]
    assert results["test_app"].items == ()


def test_reviews_fake_when_consumer_mixes_moved_access_and_forwarding(
    tmp_path: Path,
) -> None:
    # Given a consumer requiring two incompatible shapes after its direct call moves.
    sources = {
        "provider": "from daytona import Daytona\ndef launch(client): return client.create()\ndef consume(client: Daytona):\n    return client.create() + launch(client)\n",
        "test_app": "from types import SimpleNamespace\nfrom unittest.mock import Mock\nfrom provider import consume\ndef test_consume():\n    fake = SimpleNamespace(create=Mock(return_value=42))\n    assert consume(fake) == 84\n",
    }
    # When forwarding cannot certify automatic adaptation.
    results = migrate_project(sources, tmp_path)
    # Then preserve the fake and report the existing manual obligation.
    assert ast.dump(ast.parse(results["test_app"].source)) == ast.dump(
        ast.parse(sources["test_app"])
    )
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)


@pytest.mark.parametrize("delivery", ["return", "yield"])
@pytest.mark.parametrize(
    "factory",
    [
        "MagicMock(spec=Daytona)",
        "MagicMock(spec_set=Daytona)",
        "create_autospec(Daytona, instance=True)",
        "create_autospec(Daytona)",
    ],
)
def test_routes_spec_mock_when_injected_from_conftest(
    tmp_path: Path, delivery: str, factory: str
) -> None:
    # Given the same supported mock as inline, but returned/yielded by a real fixture.
    sources = {
        "provider": "from daytona import Daytona\ndef fetch(client: Daytona, sid): return client.get(sid)\n",
        "conftest": "import pytest\nfrom unittest.mock import MagicMock, create_autospec\nfrom daytona import Daytona\n@pytest.fixture\ndef client():\n    "
        + delivery
        + " "
        + factory
        + "\n",
        "test_app": "from provider import fetch\ndef test_fetch(client):\n    client.get.return_value = 'found'\n    assert fetch(client, 'sandbox-id') == 'found'\n    client.get.assert_called_once_with('sandbox-id')\n",
    }
    results = migrate_project(sources, tmp_path)
    (tmp_path / "daytona.py").write_text(
        "class Daytona:\n    @property\n    def v0(self): raise AssertionError('real SDK must not be called')\n"
    )
    # When pytest actually resolves conftest and production reads the mock's namespace.
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    # Then setup, call and assertion share the same non-vacuous child mock.
    assert run.returncode == 0, run.stdout + run.stderr
    assert all(not result.items for result in results.values())


@pytest.mark.parametrize("delivery", ["return", "yield"])
@pytest.mark.parametrize("spec", ["AsyncDaytona", "Daytona, **options"])
def test_reviews_fixture_when_mock_spec_is_uncertain(
    tmp_path: Path, delivery: str, spec: str
) -> None:
    # Given an async or unresolved spec that the inline path cannot safely rewrite.
    sources = {
        "conftest": "import pytest\nfrom unittest.mock import MagicMock\nfrom daytona import Daytona, AsyncDaytona\n@pytest.fixture\ndef client():\n    "
        + delivery
        + " MagicMock(spec="
        + spec
        + ")\n",
        "test_app": "def test_fetch(client):\n    client.get.return_value = 'found'\n",
    }
    # When fixture evidence crosses the injection boundary.
    results = migrate_project(sources, tmp_path)
    # Then the setup is reviewed, never silently accepted or guessed.
    assert results["test_app"].source == sources["test_app"]
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)


def test_reviews_fixture_when_only_some_returns_are_spec_mocks(tmp_path: Path) -> None:
    # Given a supported spec on one branch but an unrelated object on another.
    sources = {
        "conftest": "import pytest\nfrom unittest.mock import MagicMock\nfrom daytona import Daytona\n@pytest.fixture\ndef client():\n    if flag: return MagicMock(spec=Daytona)\n    return foreign\n",
        "test_app": "def test_fetch(client):\n    client.get.return_value = 'found'\n",
    }
    # When fixture summarization cannot prove a single mock shape.
    results = migrate_project(sources, tmp_path)
    # Then its uses remain reviewable without a guessed namespace.
    assert results["test_app"].source == sources["test_app"]
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)


def test_reviews_fixture_when_configuration_invalidates_the_mock(
    tmp_path: Path,
) -> None:
    # Given the inline path's unsupported dynamic configuration before returning a mock.
    sources = {
        "conftest": "import pytest\nfrom unittest.mock import MagicMock\nfrom daytona import Daytona\n@pytest.fixture\ndef client():\n    fake = MagicMock(spec=Daytona)\n    fake.configure_mock(**options)\n    return fake\n",
        "test_app": "def test_fetch(client):\n    client.get.return_value = 'found'\n",
    }
    # When return inference must preserve the same mutation fence as inline usage.
    results = migrate_project(sources, tmp_path)
    # Then fixture injection cannot resurrect a discarded rewrite proof.
    assert results["test_app"].source == sources["test_app"]
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)


@pytest.mark.parametrize("suite", ["except", "match"])
def test_reviews_module_fake_when_alias_cannot_be_inserted(
    tmp_path: Path, suite: str
) -> None:
    if suite == "match" and sys.version_info < (3, 10):
        pytest.skip("match syntax requires Python 3.10")
    # Given an assignment in a simple suite, not a standalone statement line.
    binding = (
        "    try:\n        raise RuntimeError('offline')\n    except RuntimeError: fake = SimpleNamespace()\n"
        if suite == "except"
        else "    match 1:\n        case 1: fake = SimpleNamespace()\n"
    )
    sources = {
        "provider": "def load():\n    import daytona\n    return daytona\ndef error_type(): return load().common.errors.DaytonaError\n",
        "test_app": "from types import SimpleNamespace\nfrom unittest.mock import patch\nfrom provider import error_type\ndef test_error():\n"
        + binding
        + "    fake.common = SimpleNamespace(errors=SimpleNamespace(DaytonaError=ValueError))\n    with patch('provider.load', return_value=fake):\n        assert error_type() is ValueError\n",
    }
    # When the transformer cannot install the required self-alias in that suite.
    results = migrate_project(sources, tmp_path)
    # Then it preserves the fake and surfaces manual review instead of breaking setup.
    assert ast.dump(ast.parse(results["test_app"].source)) == ast.dump(
        ast.parse(sources["test_app"])
    )
    assert any(item["kind"] == "mock-target" for item in results["test_app"].items)
