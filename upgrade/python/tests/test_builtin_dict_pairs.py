# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Builtin dict copies retain typed and positional client evidence."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import build_index
from daytona_upgrade.models import load_mapping
from daytona_upgrade.resolution import Resolver


def test_imported_builtin_dict_is_classified_as_a_mapping_constructor() -> None:
    # Given the production resolver and the project's configured SDK mapping.
    mapping = load_mapping()
    resolver = Resolver(mapping, build_index({}, mapping))
    # When resolving an explicit builtins import, as used by both import forms.
    imported_kind = resolver.imported("builtins", "dict")
    # Then its call enters the builtin dict evidence path.
    assert imported_kind == "module:builtins.dict"


@pytest.mark.parametrize(
    "import_line,copy",
    [
        ("from builtins import dict as copy_dict", "copy_dict"),
        ("import builtins", "builtins.dict"),
    ],
)
def test_reports_client_after_builtin_mapping_copy(import_line: str, copy: str) -> None:
    # Given a Daytona mapping copied through an explicitly qualified builtin.
    source = (
        "from daytona import Daytona\n"
        + import_line
        + "\ndef run(source: dict[str, Daytona]):\n"
        + f"    copied = {copy}(source)\n"
        + "    copied['client'].create()\n"
    )
    # When migrating the copied mapping's client call.
    result = migrate(source)
    # Then the evidence survives as manual review instead of disappearing.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "pairs,access",
    [
        ('[("client", client)]', "copied['client'].create()"),
        (
            '(("client", client),)',
            "for value in copied.values():\n        value.create()",
        ),
        ('[(client, "name")]', "for value in copied:\n        value.create()"),
    ],
)
def test_reports_client_from_literal_pair_iterable(pairs: str, access: str) -> None:
    # Given dict() input expressed as literal two-item tuples.
    source = (
        "from daytona import Daytona\n"
        "def run(client: Daytona):\n"
        f"    copied = dict({pairs})\n"
        f"    {access}\n"
    )
    # When migrating the resulting mapping's client use.
    result = migrate(source)
    # Then the pair's key/value position remains visible as a FIXME.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "pairs,access,expected",
    [
        ('[("client", client)]', "copied['client'].create()", ["fixme"]),
        ('[("name", "value")]', "copied['name'].create()", []),
        ("pairs", "copied['name'].create()", ["fixme"]),
    ],
)
def test_other_dict_iterable_is_uncertain_only_with_client_evidence(
    pairs: str, access: str, expected: list[str]
) -> None:
    # Given literal or unresolved pair input, with typed element provenance as applicable.
    source = (
        "from daytona import Daytona\n"
        "def run(client: Daytona, pairs: list[tuple[str, Daytona]]):\n"
        f"    copied = dict({pairs})\n"
        f"    {access}\n"
    )
    # When analyzing the resulting lookup.
    result = migrate(source)
    # Then possible client evidence produces review, and known nonclients do not.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == expected


def test_reports_client_from_zip_pair_iterable() -> None:
    # Given zip() pairs one ordinary key iterable with a client iterable.
    source = (
        "from daytona import Daytona\n"
        "def run(names: list[str], clients: list[Daytona]):\n"
        "    copied = dict(zip(names, clients))\n"
        "    for value in copied.values():\n"
        "        value.create()\n"
    )
    # When migrating the client carried by the generated pairs.
    result = migrate(source)
    # Then the unknowable pair position is reported, never silently dropped.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
