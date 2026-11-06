# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Prefix memoization must retain exceptional-path and nested-write uncertainty."""

from __future__ import annotations

from time import perf_counter

from daytona_upgrade.flow import build_index
from daytona_upgrade.models import load_mapping
from daytona_upgrade.prefix_proofs import PrefixProofs
from daytona_upgrade.resolution import ProjectIndex, Resolver


def test_retains_uncertainty_when_a_binding_changes_after_an_observation() -> None:
    # Given a prefix observed with a proven client.
    observer = PrefixProofs(Resolver(load_mapping(), ProjectIndex({})))
    prefixes = {"sdk": "client"}
    env = {"sdk": "client"}
    observer.observe((prefixes,), env)
    env["sdk"] = "non-client"
    # When a later AST visit sees a replaced binding in the same dictionary.
    observer.observe((prefixes,), env)
    # Then it preserves historical client evidence rather than stale certainty.
    assert prefixes == {"sdk": "unproven:client"}


def test_recomputes_when_an_accumulator_changes_with_the_same_environment() -> None:
    # Given unchanged bindings but a prefix updated by a surrounding join.
    observer = PrefixProofs(Resolver(load_mapping(), ProjectIndex({})))
    prefixes = {"sdk": "client"}
    env = {"sdk": "client"}
    observer.observe((prefixes,), env)
    prefixes.clear()
    # When another visit observes that changed accumulator.
    observer.observe((prefixes,), env)
    # Then the missing prefix cannot preserve the client's current certainty.
    assert prefixes == {"sdk": "unproven:client"}


def test_rechecks_alias_when_only_its_dependency_changes() -> None:
    # Given an alias that initially has no client evidence on either path.
    observer = PrefixProofs(Resolver(load_mapping(), ProjectIndex({})))
    prefixes: dict[str, str] = {}
    env = {"Alias": "alias:Client", "Client": "non-client"}
    observer.observe((prefixes,), env)
    env["Client"] = "type:client"
    # When a later store changes only the referenced name.
    observer.observe((prefixes,), env)
    # Then the alias also gains uncertain client evidence across prefixes.
    assert prefixes["Alias"] == "annotation:unproven:client"


def test_unrelated_mock_file_is_fast_when_repeated_expressions_share_prefixes() -> None:
    # Given a 1,855-line unlinked mock module whose literal aliases enter prefix joins.
    lines = ["from unittest.mock import patch"]
    lines.extend(
        f"ALIAS_{number} = 'unrelated_literal_{number}'" for number in range(100)
    )
    lines.extend(["def test_catalog():", "    with patch(target):"])
    lines.extend("        inspect(catalog)" for _ in range(1855 - len(lines)))
    source = "\n".join(lines) + "\n"
    mapping = load_mapping()
    sources = {
        "app": "from daytona import Daytona\nclient = Daytona()\nclient.create()\n",
        "test_catalog": source,
    }
    # When the real index retains the mock module and analyzes its prefixes.
    started = perf_counter()
    index = build_index(sources, mapping)
    elapsed = perf_counter() - started
    # Then optimization does not skip the module or introduce client evidence.
    assert ("test_catalog", source) not in index.skipped_sources
    assert index.exports["app"]["client"] == "client"
    assert elapsed < 20, f"unrelated mock module took {elapsed:.2f}s"
