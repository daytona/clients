# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fixture names supply evidence only in pytest injection scopes."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def test_production_stays_clean_when_parameters_share_fixture_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a root client fixture and 25 ordinary production consumers.
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom daytona import Daytona\n"
        "@pytest.fixture\ndef client():\n    return Daytona()\n"
    )
    source = "\n".join(
        f"def fetch_{number}(client):\n    return client.get('https://example.com')\n"
        for number in range(5)
    )
    modules = [tmp_path / f"app_{number}.py" for number in range(5)]
    for module in modules:
        module.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When upgrading the whole project through its strict CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then unrelated production calls cause neither rewrites nor manual items.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert all(module.read_text() == source for module in modules)


@pytest.mark.parametrize(
    "consumer",
    [
        "def test_fetch(client):\n    client.get('id')\n",
        "def testfetch(client):\n    client.get('id')\n",
        "async def test_fetch(client):\n    client.get('id')\n",
        "class TestAPI:\n    def test_fetch(self, client):\n        client.get('id')\n",
        "class TestAPI:\n    async def test_fetch(self, client):\n        client.get('id')\n",
        "import pytest\n@pytest.fixture\ndef resource(client):\n    client.get('id')\n",
        "import pytest\n@pytest.fixture()\ndef resource(client):\n    client.get('id')\n",
        "from pytest import fixture\n@fixture\ndef resource(client):\n    client.get('id')\n",
        "from pytest import fixture\n@fixture(scope='session')\ndef resource(client):\n    client.get('id')\n",
    ],
)
def test_fixture_requires_review_when_function_is_pytest_injected(
    consumer: str,
) -> None:
    # Given an injectable consumer in a collected test module.
    mapping = load_mapping()
    sources = {
        "conftest": (
            "import pytest\nfrom daytona import Daytona\n"
            "@pytest.fixture\ndef client():\n    return Daytona()\n"
        ),
        "test_app": consumer,
    }
    context = SourceContext("test_app.py", "test_app", build_index(sources, mapping))
    # When resolving the same fixture parameter through normal project analysis.
    result = transform_source(consumer, TransformOptions(mapping), context)
    # Then the injected client needs review, never an automatic rewrite.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(consumer))


def test_fixture_requires_review_when_function_is_in_conftest() -> None:
    # Given an ordinary function defined in conftest alongside a client fixture.
    source = (
        "import pytest\nfrom daytona import Daytona\n"
        "@pytest.fixture\ndef client():\n    return Daytona()\n"
        "def configure(client):\n    client.get('id')\n"
    )
    mapping = load_mapping()
    context = SourceContext(
        "tests/conftest.py",
        "tests.conftest",
        build_index({"tests.conftest": source}, mapping),
    )
    # When analyzing the conftest module.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then its parameter retains fixture evidence without a rewrite proof.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "consumer",
    [
        "def fetch(client):\n    client.get('id')\n",
        "async def fetch(client):\n    client.get('id')\n",
        "class API:\n    def fetch(self, client):\n        client.get('id')\n",
        "class TestAPI:\n    def test_outer(self):\n        def fetch(client):\n            client.get('id')\n",
        "def test_outer():\n    def fetch(client):\n        client.get('id')\n",
        "class TestAPI:\n    pass\nclass API:\n    def fetch(self, client):\n        client.get('id')\n",
        "def fixture(fn):\n    return fn\n@fixture\ndef fetch(client):\n    client.get('id')\n",
    ],
)
def test_helpers_stay_clean_when_fixture_scope_does_not_apply(consumer: str) -> None:
    # Given non-injected helpers, including helpers inside injectable scopes.
    mapping = load_mapping()
    context = SourceContext(
        "test_app.py",
        "test_app",
        build_index(
            {
                "conftest": (
                    "import pytest\nfrom daytona import Daytona\n"
                    "@pytest.fixture\ndef client():\n    return Daytona()\n"
                ),
                "test_app": consumer,
            },
            mapping,
        ),
    )
    # When resolving names in a test module without an injection contract.
    result = transform_source(consumer, TransformOptions(mapping), context)
    # Then names and enclosing scopes alone do not introduce fixture evidence.
    assert not result.items
    assert result.source == consumer


@pytest.mark.parametrize(
    "consumer",
    [
        "class TestAPI:\n    def fetch(self, client):\n        client.get('id')\n",
        "class Helpers:\n    def test_fetch(self, client):\n        client.get('id')\n",
    ],
)
def test_fixture_stays_unchanged_when_method_is_not_collected(consumer: str) -> None:
    # Given a helper method pytest never collects, so it is never injected.
    mapping = load_mapping()
    sources = {
        "conftest": (
            "import pytest\nfrom daytona import Daytona\n"
            "@pytest.fixture\ndef client():\n    return Daytona()\n"
        ),
        "app": consumer,
    }
    context = SourceContext("app.py", "app", build_index(sources, mapping))
    # When resolving the same-named parameter.
    result = transform_source(consumer, TransformOptions(mapping), context)
    # Then no fixture evidence applies.
    assert result.source == consumer
    assert result.items == ()
