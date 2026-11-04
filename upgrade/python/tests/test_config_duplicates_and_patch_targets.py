# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Call-boundary regressions for effective configuration and mock targets."""

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
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


def test_config_uses_effective_value_when_literal_key_is_repeated() -> None:
    # Given a custom URL overwritten by the Cloud URL in the same dictionary.
    source = (
        "from daytona import DaytonaConfig\n"
        "config = DaytonaConfig(**{'api_url': 'https://private.example/api', "
        "'api_url': 'https://app.daytona.io/api', 'api_key': key})\n"
    )
    # When the configuration is migrated.
    result = migrate(source)
    # Then both URL entries disappear without refusing an effective Cloud config.
    assert result.source == (
        "from daytona import DaytonaConfig\n"
        "config = DaytonaConfig(**{'api_key': key})\n"
    )
    assert result.items == ()


def test_config_refuses_when_effective_duplicate_is_custom() -> None:
    # Given the reverse overwrite, whose effective value is unsafe.
    source = (
        "from daytona import DaytonaConfig\n"
        "config = DaytonaConfig(**{'api_url': 'https://app.daytona.io/api', "
        "'api_url': 'https://private.example/api'})\n"
    )
    # When the effective configuration is checked.
    result = migrate(source)
    # Then the custom URL is still reported, so the CLI will refuse all writes.
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


def test_config_stays_user_owned_when_shadowed_value_may_have_effects() -> None:
    # Given a shadowed expression that still executes during dict construction.
    source = (
        "from daytona import DaytonaConfig\n"
        "config = DaytonaConfig(**{'api_url': record_url(), "
        "'api_url': 'https://app.daytona.io/api'})\n"
    )
    # When checking whether both URL entries can be removed safely.
    result = migrate(source)
    # Then migration refuses rather than silently discarding the evaluation.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


@pytest.mark.parametrize(
    "call,expected",
    [
        (
            "patch(new=mock, target='daytona.Sandbox')",
            "patch(new=mock, target='daytona.v0.Sandbox')",
        ),
        (
            "patch(target='daytona.Sandbox', new=mock)",
            "patch(target='daytona.v0.Sandbox', new=mock)",
        ),
    ],
)
def test_patch_rewrites_when_target_is_keyword(call: str, expected: str) -> None:
    # Given the supported mock API with a keyword target in either position.
    imports = "from unittest.mock import patch\nimport daytona\n"
    # When mock paths are migrated.
    result = migrate(imports + call + "\n")
    # Then the target, not the new mock object, is rewritten.
    assert result.source == imports + expected + "\n"


@pytest.mark.parametrize("call", ["patch(target_path)", "patch(target=target_path)"])
def test_patch_reports_when_target_is_dynamic(call: str) -> None:
    # Given a mock target that cannot be proven statically.
    source = "from unittest.mock import patch\nimport daytona\n" + call + "\n"
    # When inspecting the call.
    result = migrate(source)
    # Then the unchanged path needs explicit manual review.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
