# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Local fake-module ownership permits namespace edits, never arbitrary escapes."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(body: str) -> SourceResult:
    source = (
        "import provider\nimport types\nfrom types import SimpleNamespace\n"
        "from unittest.mock import MagicMock, Mock, patch\n"
        "def test_module(monkeypatch):\n" + body
    )
    mapping = load_mapping()
    index = build_index(
        {
            "test_app": source,
            "provider": "def load():\n    import daytona\n    return daytona\n",
        },
        mapping,
    )
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", index),
    )


@pytest.mark.parametrize(
    "factory",
    ["MagicMock()", "Mock()", "SimpleNamespace()", 'types.ModuleType("fake")'],
)
@pytest.mark.parametrize(
    "install",
    [
        'monkeypatch.setattr("provider.load", lambda: fake)',
        'monkeypatch.setattr(provider, "load", lambda: fake)',
        'patch("provider.load", return_value=fake)',
        'patch("provider.load", side_effect=lambda: fake)',
        'patch("provider.load", new=lambda: fake)',
        'patch.object(provider, "load", new=Mock(return_value=fake))',
    ],
)
def test_routes_module_paths_when_local_fake_only_replaces_module_helper(
    factory: str, install: str
) -> None:
    # Given a local fake with Harbor's error-classification fixture shape.
    body = f"    fake = {factory}\n    fake.common.errors.DaytonaError = Error\n    fake._async.snapshot.State = State\n    fake._sync.client = client\n    {install}\n    fake.common.errors.DaytonaError\n"
    # When migrating both the module provider and its bounded replacement.
    result = migrate(body)
    # Then the fake supplies precisely the namespace read by migrated production code.
    assert "fake.v0.common.errors.DaytonaError = Error" in result.source
    assert "fake.v0._async.snapshot.State = State" in result.source
    assert "fake.v0._sync.client = client" in result.source
    assert result.source.endswith("    fake.v0.common.errors.DaytonaError\n")
    assert result.items == ()


@pytest.mark.parametrize(
    "escape",
    [
        "consume(fake)",
        "alias = fake",
        "getattr(fake, path)",
        "fake.other.read()",
        "fake = foreign",
        "def later(): return fake",
        "return fake",
        "from foreign import fake",
        "import foreign as fake",
        "def fake(): pass",
    ],
)
def test_reviews_module_replacement_when_fake_ownership_is_unproven(
    escape: str,
) -> None:
    # Given a module replacement whose object has another unproven flow.
    body = f'    fake = MagicMock()\n    fake.common.errors.Error = Error\n    monkeypatch.setattr("provider.load", lambda: fake)\n    {escape}\n'
    # When the local-only proof fails.
    result = migrate(body)
    # Then the fake stays intact and the patch keeps its review item.
    assert "fake.v0" not in result.source
    assert [item["kind"] for item in result.items] == ["mock-target"]
