# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Source slicing stays exact without rescanning the file for every call."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import Flow, SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import Resolver
from daytona_upgrade.source_text import SourceText
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize(
    "body",
    [
        "résultat = café.client.create()",
        "résultat = (\n café\n .client\n).create()",
        "résultat = factory('a\\fb\\vc\\u2028d').client.create()",
        "résultat = factory('a\fb\vc\u2028d').client.create()",
    ],
)
def test_receivers_match_stdlib_when_source_has_unicode_and_parser_newlines(
    body: str, newline: str
) -> None:
    # Given expressions whose AST columns count bytes, not characters.
    source = body.replace("\n", newline) + newline
    mapping = load_mapping()
    flow = Flow(
        Resolver(mapping, build_index({}, mapping)),
        SourceContext("app.py", "app", build_index({}, mapping)),
    )
    # When the real flow captures each receiver.
    flow.analyze(source)
    # Then receiver text is byte-for-byte equivalent to the stdlib reference.
    assert [call.receiver for call in flow.calls] == [
        ast.get_source_segment(source, call.node.func.value)
        for call in flow.calls
        if isinstance(call.node.func, ast.Attribute)
    ]


def test_analysis_avoids_whole_source_slicing_when_many_receivers_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a file with repeated SDK calls and a forbidden quadratic operation.
    source = "from daytona import Daytona\nd = Daytona()\n" + "d.create()\n" * 64
    mapping = load_mapping()

    def whole_source_slice(source: str, node: ast.AST) -> str | None:
        raise AssertionError("receiver lookup rescanned the complete source")

    monkeypatch.setattr(ast, "get_source_segment", whole_source_slice)
    # When index construction and migration both run.
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    result = transform_source(source, TransformOptions(mapping), context)
    # Then all calls are migrated without whole-source receiver lookups.
    assert result.source.count("d.v0.create()") == 64


def test_receiver_cache_is_replaced_when_flow_analyzes_a_new_source() -> None:
    # Given an analyzer that can be reused with another source.
    mapping = load_mapping()
    index = build_index({}, mapping)
    flow = Flow(Resolver(mapping, index), SourceContext("app.py", "app", index))
    flow.analyze("old.create()\n")
    # When a different source is analyzed with the same visitor.
    flow.analyze("nouveau.create()\n")
    # Then the second receiver comes from the current source, not the old cache.
    assert flow.calls[-1].receiver == "nouveau"


def test_segment_results_match_stdlib_when_every_expression_is_sliced() -> None:
    # Given nested multiline expressions with non-ASCII byte offsets.
    source = "résultat = café(\r\n 'é\u2028x',\r\n other['clé'].create(),\r\n)\r\n"
    text = SourceText.parse(source)
    expressions = [
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.expr)
    ]
    # When the cached offset index supplies each exact expression span.
    results = [text.segment(node) for node in expressions]
    # Then both single-line and multiline slices match the stdlib reference.
    assert results == [ast.get_source_segment(source, node) for node in expressions]


def test_segment_is_absent_when_ast_location_is_missing() -> None:
    # Given a synthetic expression without parser location attributes.
    text = SourceText.parse("value\n")
    node = ast.Name(id="value")
    # When the source segment is requested.
    result = text.segment(node)
    # Then it has the same absence contract as the stdlib helper.
    assert result is None
