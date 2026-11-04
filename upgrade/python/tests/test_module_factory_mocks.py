# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mocked module factories require replacements with SDK 2.x namespace layout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def test_strict_rewrites_fake_when_harbor_fixture_replaces_lazy_module_helper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given Harbor's real provider and its verbatim fake-module fixture.
    provider = tmp_path / "src/harbor/environments/daytona/utils.py"
    provider.parent.mkdir(parents=True)
    provider.write_text(
        (Path(__file__).parent / "fixtures/harbor_utils.py").read_text()
    )
    fixture = """from unittest.mock import MagicMock
import pytest

class FakeDaytonaError(Exception):
    pass

class FakeDaytonaRateLimitError(FakeDaytonaError):
    pass

class FakeDaytonaConflictError(FakeDaytonaError):
    pass

@pytest.fixture
def fake_daytona_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.common.errors.DaytonaError = FakeDaytonaError
    fake.common.errors.DaytonaRateLimitError = FakeDaytonaRateLimitError
    fake.common.errors.DaytonaConflictError = FakeDaytonaConflictError
    monkeypatch.setattr(
        "harbor.environments.daytona.utils._get_daytona",
        lambda: fake,
    )
"""
    app = tmp_path / "tests/unit/environments/test_daytona_utils.py"
    app.parent.mkdir(parents=True)
    app.write_text(fixture)
    (tmp_path / "requirements.txt").write_text("daytona==0.210.0\n")
    # When reviewing the complete project through the real strict CLI.
    status = main([str(tmp_path), "--yes", "--json", "--strict"])
    # Then the owned fake supplies the migrated module namespace without review.
    report = json.loads(capsys.readouterr().out)
    items = [
        item
        for item in report["items"]
        if item["file"] == app.relative_to(tmp_path).as_posix()
    ]
    assert status == 0
    assert items == []
    assert app.read_text() == fixture.replace("fake.common", "fake.v0.common")


@pytest.mark.parametrize(
    "operation",
    [
        'monkeypatch.setattr("provider.load", lambda: fake)',
        'monkeypatch.setattr(provider, "load", lambda: fake)',
        'monkeypatch.setattr(target=provider, name="load", value=lambda: fake)',
        'mock.patch("provider.load", return_value=fake)',
        'patch("provider.load", return_value=fake)',
        'patch(target="provider.load", return_value=fake)',
        'patch.object(provider, "load", return_value=fake)',
        'patch.object(attribute="load", target=provider, return_value=fake)',
        '@patch("provider.load", return_value=fake)\ndef test_run(replacement): pass',
        '@patch.object(provider, "load", return_value=fake)\ndef test_run(replacement): pass',
        'with patch("provider.load", return_value=fake):\n    pass',
        'mock.patch("provider." "load", return_value=fake)',
        'replace("provider.load", return_value=fake)',
        'um.patch.object(provider, "load", return_value=fake)',
    ],
)
def test_reports_module_factory_when_patch_target_resolves_to_summary(
    operation: str,
) -> None:
    # Given canonical and aliased mock targets of a project module factory.
    source = (
        "import provider\nfrom unittest import mock\nfrom unittest.mock import patch\n"
        "from unittest.mock import patch as replace\nimport unittest.mock as um\n"
        + operation
        + "\n"
    )
    mapping = load_mapping()
    sources = {
        "provider": "def load():\n    import daytona\n    return daytona\n",
        "test_app": source,
    }
    # When inspecting string/object targets with the existing project index.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", build_index(sources, mapping)),
    )
    # Then each patch gets one review item without speculative fake rewrites.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
    assert result.items[0]["message"] == (
        "Mock target replaces `provider.load`, which can return the daytona module; "
        "SDK 2.x reads 0.x paths under .v0, so give the replacement a v0 attribute "
        "(for example fake.v0.common.errors)."
    )


def test_reports_reexported_factory_when_summary_is_review_only() -> None:
    # Given an aliased export whose factory returns Daytona on only some paths.
    source = (
        'from unittest.mock import patch\npatch("exports.lazy", return_value=fake)\n'
    )
    mapping = load_mapping()
    sources = {
        "provider": "import daytona\ndef load(flag):\n    if flag:\n        return daytona\n    return None\n",
        "exports": "from provider import load as lazy\n",
        "test_app": source,
    }
    # When resolving a dotted mock target through the existing exported summaries.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", build_index(sources, mapping)),
    )
    # Then even a possible module replacement retains the namespace review.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
    assert "`exports.lazy`" in result.items[0]["message"]


@pytest.mark.parametrize(
    "provider",
    [
        "def load():\n    import daytona\n    return None\n",
        "def load():\n    import other\n    return other\n",
    ],
)
@pytest.mark.parametrize(
    "operation",
    [
        'patch("provider.load")',
        'patch.object(provider, "load")',
        'monkeypatch.setattr(provider, "load", fake)',
    ],
)
def test_preserves_unrelated_mock_when_factory_does_not_return_daytona(
    provider: str, operation: str
) -> None:
    # Given a project function with the same name but no module-return summary.
    source = "import provider\nfrom unittest.mock import patch\n" + operation + "\n"
    mapping = load_mapping()
    # When checking the function identity instead of its target's spelling.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext(
            "test_app.py",
            "test_app",
            build_index({"test_app": source, "provider": provider}, mapping),
        ),
    )
    # Then no unrelated patch gains a module-layout review or rewrite.
    assert result.source == source
    assert result.items == ()
