# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal merges use their effective payload without discarding evaluation."""

from __future__ import annotations

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize(
    "call",
    [
        "DaytonaConfig.model_validate({**{'api_url': custom}, 'api_url': None, 'api_key': 'k'})",
        "cfg.model_copy(update={**{'api_url': 'https://custom.example/api'}, 'api_url': None})",
        "cfg.model_copy(update={'api_url': 'https://custom.example/api', **{'api_url': None}})",
        "cfg.model_copy(**{**{'update': {'api_url': 'https://custom.example/api'}}, 'update': {'api_key': 'k'}})",
        "cfg.model_copy(**{'update': {'api_url': 'https://custom.example/api'}, **{'update': {'api_key': 'k'}}})",
        "cfg.model_copy(**{'update': {'api_url': 'https://custom.example/api'}}, update={'api_key': 'k'})",
        "cfg.model_copy(update={'api_url': 'https://custom.example/api'}, **{'update': {'api_key': 'k'}})",
        "DaytonaConfig.model_construct(**{'api_url': 'https://custom.example/api'}, api_url=None)",
    ],
)
def test_cloud_resolves_when_literal_merge_overwrites_url(call: str) -> None:
    # Given a literal merge whose final payload has no custom URL.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona({call}).create()\n"
    )
    # When inspecting the effective mapping, including nested and call expansions.
    result = migrate(source)
    # Then dead custom values do not invent a URL refusal.
    assert not any(item["kind"] == "custom-api-url" for item in result.items)
    assert any(item["kind"] == "config-field" for item in result.items)


@pytest.mark.parametrize(
    "call",
    [
        "cfg.model_copy(update={'api_url': None, **options})",
        "cfg.model_copy(update={'api_url': None, **{'api_key': 'k'}, **options})",
        "cfg.model_copy(**{'update': {'api_url': None}, **options})",
        "cfg.model_copy(update={**{'api_url': None}, 'api_url': 'https://custom.example/api'})",
    ],
)
@pytest.mark.parametrize("drop", [False, True])
def test_refusal_when_later_merge_can_supply_custom_url(call: str, drop: bool) -> None:
    # Given a later unknown mapping or an effective known-custom URL.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona({call}).create()\n"
    )
    # When inspecting the final payload rather than trusting an earlier default.
    result = migrate(source, drop=drop)
    # Then the merge cannot silently bypass URL checks.
    if drop and "options" in call:
        assert call in result.source
        assert any(item["kind"] == "config-field" for item in result.items)
    else:
        assert any(item["kind"] == "custom-api-url" for item in result.items)


def test_payload_stays_manual_when_overwritten_url_has_effects() -> None:
    # Given an overwritten expression that still runs while building the mapping.
    payload = "{**{'api_url': compute_url()}, 'api_url': None}"
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"Daytona(DaytonaConfig.model_validate({payload})).create()\n"
    )
    # When the final URL is safe but discarding the old evaluation would not be.
    result = migrate(source)
    # Then a manual item preserves the payload without inventing custom URL evidence.
    assert payload in result.source
    assert not any(item["kind"] == "custom-api-url" for item in result.items)
    assert any(item["kind"] == "config-field" for item in result.items)


@pytest.mark.parametrize(
    "call",
    [
        "cfg.model_copy(**{'update': {'api_url': 'https://custom.example/api'}}, update={'api_key': 'k'})",
        "DaytonaConfig.model_construct(**{'api_url': 'https://custom.example/api'}, api_url=None)",
    ],
)
def test_call_stays_manual_when_keywords_duplicate_at_runtime(call: str) -> None:
    # Given conflicting call keywords, which Python rejects rather than merging.
    source = (
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"Daytona({call}).create()\n"
    )
    # When analyzing their final payload for URL evidence.
    result = migrate(source)
    # Then analysis never silently repairs the runtime error into a different call.
    assert call in result.source
    assert any(item["kind"] == "config-field" for item in result.items)


@pytest.mark.parametrize("url", ["api_url", "server_url"])
def test_shadowed_url_is_removed_when_effective_default_is_removed(url: str) -> None:
    # Given nested constant merges whose final value selects Cloud.
    payload = f"{{**{{'{url}': 'https://custom.example/api'}}, '{url}': None}}"
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"Daytona(DaytonaConfig.model_validate({payload})).create()\n"
    )
    # When stripping the effective obsolete URL field.
    result = migrate(source)
    # Then removing the default cannot expose the earlier custom URL again.
    assert url not in result.source
    assert not any(item["kind"] == "custom-api-url" for item in result.items)
