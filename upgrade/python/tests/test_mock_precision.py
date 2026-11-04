# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mock helpers and targets require the appropriate provenance."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext(
            "test_app.py", "test_app", build_index({"test_app": source}, mapping)
        ),
    )


@pytest.mark.parametrize(
    "operation",
    [
        'patch.object(os, "getcwd")',
        'patch.object(os.path, "exists")',
        'patch.multiple("os", getcwd=fake)',
        "patch.multiple(Cache, get=fake, list=other)",
        "patch.multiple(Cache, get=fake, close=other)",
        'monkeypatch.setattr(os, "getcwd", fake)',
        'mocker.patch.object(os.path, "exists")',
        'patch.object(Cache(), "get")',
        'patch.object(Cache, "get")',
    ],
)
def test_mock_stays_unchanged_when_target_has_no_daytona_link(operation: str) -> None:
    # Given unrelated mocks alongside real SDK usage.
    source = (
        "from unittest.mock import patch\nimport os\nfrom daytona import Daytona\nclass Cache: pass\nd = Daytona()\n"
        + operation
        + "\n"
    )
    # When applying precision-first target review.
    result = migrate(source)
    # Then ordinary mocks neither change nor block strict mode.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "setup,operation",
    [
        ("def patch(target):\n    return target", 'patch("daytona.Image")'),
        ("def patch(target):\n    pass", 'patch("daytona.Image")'),
        ("patch = user_function", 'patch("daytona.Image")'),
        ("from user_helpers import patch", 'patch("daytona.Image")'),
        ("import user_helpers as mock", 'mock.patch("daytona.Image")'),
        ("mocker = user_object", 'mocker.patch("daytona.Image")'),
    ],
)
def test_patch_stays_user_owned_when_binding_is_not_mock_helper(
    setup: str, operation: str
) -> None:
    # Given a same-spelled helper with no mock-library binding.
    source = f"from daytona import Daytona\n{setup}\n{operation}\n"
    # When inspecting its ordinary string data.
    result = migrate(source)
    # Then the argument is neither rewritten nor reported as a mock target.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("replacement", ["object()", "unknown()", "42"])
@pytest.mark.parametrize(
    "operation",
    [
        'patch.object(client, "create")',
        "patch.multiple(client, create=fake)",
        'monkeypatch.setattr(client, "create", fake)',
    ],
)
def test_mock_reports_when_receiver_has_historical_daytona_link(
    replacement: str, operation: str
) -> None:
    # Given a receiver that used to hold a client but no longer proves one.
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\nclient = Daytona()\nclient = {replacement}\n{operation}\n"
    # When checking the mock target's full value flow.
    result = migrate(source)
    # Then only mock review retains that evidence; no rewrite is certified.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_mock_reports_when_alias_carries_replaced_client_history() -> None:
    # Given an alias of a value whose SDK construction proof was lost.
    source = 'from unittest.mock import patch\nfrom daytona import Daytona\nclient = Daytona()\nclient = object()\nalias = client\npatch.object(alias, "create")\n'
    # When resolving the alias's review-only link.
    result = migrate(source)
    # Then the target is reported without a guessed compatibility namespace.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
