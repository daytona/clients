# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Collection positions and nested aliases must retain manual-review evidence."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from textwrap import indent

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize("annotation", ["dict[str, Daytona]", "dict[Daytona, str]"])
@pytest.mark.parametrize("index", ["0", "1", "position", "2", "-1", "1.0"])
@pytest.mark.parametrize(
    "body",
    [
        "for pair in clients.items():\n        pair[{index}].create()",
        "return [pair[{index}].create() for pair in clients.items()]",
    ],
)
def test_reviews_pair_when_index_may_select_client(
    annotation: str, index: str, body: str
) -> None:
    # Given distinct dictionary key/value evidence and a single pair binding.
    source = (
        "from daytona import Daytona\n"
        f"def launch(clients: {annotation}, position):\n    {body.format(index=index)}\n"
    )
    client_index = "1" if annotation == "dict[str, Daytona]" else "0"
    expected = [] if index in {"0", "1"} and index != client_index else ["fixme"]
    # When accessing a pair position through the migration surface.
    result = migrate(source)
    # Then only a proven nonclient position is silent; uncertain positions need review.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == expected


@pytest.mark.parametrize(
    "mapping",
    [
        "{Daytona(): 'value'}",
        "{'name': Daytona()}",
        "{Daytona(): Daytona()}",
        "unknown",
        "{'name': 'value'}",
        "{}",
    ],
)
@pytest.mark.parametrize("member", ["keys", "values"])
@pytest.mark.parametrize("entries", ["**clients, 'x': other", "'x': other, **clients"])
def test_reviews_spread_when_mapping_retains_client_evidence(
    mapping: str, member: str, entries: str
) -> None:
    # Given a mapping spread alongside an unrelated explicit entry.
    source = (
        "from daytona import Daytona\n"
        f"clients = {mapping}\ncombined = {{{entries}}}\n"
        f"for value in combined.{member}():\n    value.create()\n"
    )
    expected = (
        ["fixme"]
        if (member == "keys" and mapping.startswith("{Daytona()"))
        or (member == "values" and mapping.endswith("Daytona()}"))
        else []
    )
    # When migrating calls on either side of the expanded dictionary.
    result = migrate(source)
    # Then spreads preserve each side separately without inventing unknown evidence.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == expected


@pytest.mark.parametrize(
    "suite",
    [
        "if enclosing:\n{body}",
        "for unused in values:\n{body}",
        "while running:\n{body}",
        "try:\n{body}\nexcept Error:\n    pass",
        "with context:\n{body}",
    ],
)
def test_reviews_alias_when_client_binding_is_nested(suite: str) -> None:
    # Given a conditional client alias nested in branch-local compound statements.
    source = (
        "from daytona import Daytona\n"
        + suite.format(
            body=indent(
                "if outer:\n    if inner:\n        Client = Daytona\n"
                "def launch(d: Client): d.create()",
                "    ",
            )
        )
        + "\n"
    )
    # When migrating annotated calls after the relevant branch join.
    result = migrate(source)
    # Then at least the call after the inner join cannot silently lose its evidence.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "scope", ["def local():", "async def local():", "class Local:"]
)
def test_preserves_alias_when_client_write_belongs_to_nested_scope(scope: str) -> None:
    # Given a nonclient alias whose spelling is reused only in a nested scope.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"if outer:\n    {scope}\n        Client = Daytona\n"
        "def launch(d: Client): d.create()\n"
    )
    # When collecting branch-local writes.
    result = migrate(source)
    # Then nested lexical scopes cannot contaminate the outer alias.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        "clients = {'name': Daytona()}\nfor pair in clients.items():\n    pair[1].create()",
        "clients = {Daytona(): 'value'}\nfor key in {**clients, 'x': other}:\n    key.create()",
        "if outer:\n    if inner:\n        Client = Daytona\ndef launch(d: Client): d.create()",
    ],
)
def test_strict_cli_fails_when_collection_or_alias_call_needs_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    # Given a real project exercising each reported silent migration gap.
    source = "from daytona import Daytona\n" + body + "\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating in strict mode through the public CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then each unresolved call is a visible FIXME and a nonzero strict exit.
    assert status == 1
    assert [item["kind"] for item in json.loads(capsys.readouterr().out)["items"]] == [
        "fixme"
    ]
    assert "FIXME(daytona-upgrade)" in app.read_text()
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
