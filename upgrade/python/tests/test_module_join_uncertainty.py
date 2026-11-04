# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Module uncertainty survives client joins and stored factory summaries."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.attribute_proofs import _review_only
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("member", ["common.errors.Error", "create()"])
def test_reviews_opaque_result_when_join_contains_module_and_client(
    reverse: bool, member: str
) -> None:
    # Given both possible SDK identities, with no suggestive consumer name.
    left, right = ("daytona", "Daytona()")
    if reverse:
        left, right = right, left
    source = (
        "import daytona\nfrom daytona import Daytona\n"
        f"if flag:\n    value = {left}\nelse:\n    value = {right}\n"
        f"result = opaque(value)\nresult.{member}\n"
    )
    # When module-specific uncertainty crosses an opaque call after the join.
    result = migrate(source)
    # Then neither uncertainty is lost and no speculative rewrite is made.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("fixme", len(source.splitlines()))
    ]


@pytest.mark.parametrize("member", ["common.errors.Error", "create()"])
def test_reviews_imported_result_when_mixed_return_crosses_project_passes(
    member: str,
) -> None:
    # Given a consumer analyzed before the provider's mixed result is available.
    source = f"from provider import make\nvalue = opaque(make())\nvalue.{member}\n"
    mapping = load_mapping()
    sources = {
        "app": source,
        "provider": "import daytona\nfrom daytona import Daytona\ndef make():\n    if flag: return daytona\n    return Daytona()\n",
    }
    # When summaries converge and the mixed value reaches an opaque helper.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then both potential SDK surfaces remain review-only in the consumer.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == [("fixme", 3)]


def test_reviews_stored_factory_when_project_summary_is_demoted_repeatedly() -> None:
    # Given a module factory stored on a holder and overwritten opaquely.
    source = "from provider import value\nvalue.common.errors.Error\n"
    mapping = load_mapping()
    sources = {
        "app": source,
        "provider": "from leaf import load\nholder = unknown()\nholder.load = load\nholder.load = unknown\nholder.load = unknown_again\nvalue = holder.load()\n",
        "leaf": "def load():\n    import daytona\n    return daytona\n",
    }
    # When the factory is learned in a later wave, stored and demoted again.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then wrapped module review is stable and visible at the final private read.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == [("fixme", 2)]


@pytest.mark.parametrize(
    "kind",
    ["fn:module:daytona", "fn:unproven:module:daytona"],
)
def test_keeps_factory_review_when_attribute_evidence_is_demoted_again(
    kind: str,
) -> None:
    # Given an exact or previously demoted module-returning factory.
    once = _review_only(kind)
    # When a later store or indexing wave demotes its evidence again.
    twice = _review_only(once)
    # Then the callable return remains review-only rather than disappearing.
    assert twice == "fn:unproven:module:daytona"
