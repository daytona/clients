# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Pytest injection follows declaration precedence, never receiver spelling."""

from __future__ import annotations

import ast

import pytest
from upgrade_helpers import migrate_test_module as migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("override_module", ["tests.conftest", "tests.test_app"])
@pytest.mark.parametrize("result", ["Other()", "unknown()"])
def test_fixture_stays_clean_when_nearest_declaration_is_not_client(
    reverse: bool, override_module: str, result: str
) -> None:
    # Given an ancestor client fixture shadowed by a nearer non-client or unknown.
    ancestor = (
        "import pytest\nfrom daytona import Daytona\n"
        "@pytest.fixture\ndef client():\n    return Daytona()\n"
    )
    override = (
        "import pytest\nclass Other:\n    pass\n"
        f"@pytest.fixture\ndef client():\n    return {result}\n"
    )
    source = "def test_x(client):\n    client.create()\n"
    sources = {"conftest": ancestor, "tests.test_app": source}
    if override_module == "tests.test_app":
        source = override + source
        sources[override_module] = source
    else:
        sources[override_module] = override
    if reverse:
        sources = dict(reversed(list(sources.items())))
    mapping = load_mapping()
    context = SourceContext(
        "tests/test_app.py", "tests.test_app", build_index(sources, mapping)
    )
    # When resolving implicit injection through the normal project index.
    migrated = transform_source(source, TransformOptions(mapping), context)
    # Then an overridden client link causes neither a rewrite nor a false FIXME.
    assert migrated.source == source
    assert not migrated.items


@pytest.mark.parametrize("annotation", ["Other", "'Other'"])
@pytest.mark.parametrize("statement", ["return Daytona()", "yield Daytona()"])
def test_fixture_requires_review_when_non_client_annotation_conflicts(
    annotation: str, statement: str
) -> None:
    # Given a linked injected client despite a conflicting non-client annotation.
    source = (
        "import pytest\nfrom daytona import Daytona, DaytonaConfig as Other\n"
        f"@pytest.fixture\ndef client():\n    {statement}\n"
        f"def test_x(client: {annotation}):\n    client.create()\n"
    )
    # When migrating the function's receiver.
    mapping = load_mapping()
    migrated = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then injection supplies review evidence, not an automatic rewrite proof.
    assert ast.dump(ast.parse(migrated.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in migrated.items] == ["fixme"]


@pytest.mark.parametrize("reverse", [False, True])
def test_fixture_requires_review_when_nearer_client_overrides_non_client(
    reverse: bool,
) -> None:
    # Given a nearer client fixture overriding the ancestor's unrelated value.
    source = "def test_x(client):\n    client.create()\n"
    sources = {
        "conftest": "import pytest\n@pytest.fixture\ndef client():\n    return 42\n",
        "tests.conftest": (
            "import pytest\nfrom daytona import Daytona\n"
            "@pytest.fixture\ndef client():\n    return Daytona()\n"
        ),
        "tests.test_app": source,
    }
    if reverse:
        sources = dict(reversed(list(sources.items())))
    mapping = load_mapping()
    context = SourceContext(
        "tests/test_app.py", "tests.test_app", build_index(sources, mapping)
    )
    # When resolving the nearest declaration independently of indexing order.
    migrated = transform_source(source, TransformOptions(mapping), context)
    # Then the client fixture is linked, but still unproven.
    assert ast.dump(ast.parse(migrated.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in migrated.items] == ["fixme"]


def test_fixture_preserves_rewrite_when_annotation_independently_proves_client() -> (
    None
):
    # Given an injected fixture whose parameter already has a client proof.
    source = (
        "import pytest\nfrom daytona import Daytona\n"
        "@pytest.fixture\ndef client():\n    return Daytona()\n"
        "def test_x(client: Daytona):\n    client.create()\n"
    )
    # When combining annotation and injection evidence.
    migrated = migrate(source)
    # Then the existing annotation proof still authorizes its rewrite.
    assert migrated.source == source.replace("client.create()", "client.v0.create()")
