# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unrelated mapping spreads and tuple positions cannot invent client evidence."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "source",
    [
        'DEFAULTS = {"db": {"host": "x"}}\n'
        "def load(user):\n    merged = {**DEFAULTS, **user}\n"
        '    for s in merged.values(): s.get("host")\n',
        "def load(**kwargs):\n    cfg = {**kwargs}\n"
        "    for c in cfg.values(): c.list()\n",
        "from httpx import Client\n"
        "def load(clients: dict[str, Client]):\n    merged = {**clients}\n"
        '    for s in merged.values(): s.get("url")\n',
    ],
)
def test_preserves_spread_when_operand_has_no_client_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    # Given unrelated mappings in a project with a Daytona dependency, not imports.
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When writing a strict migration through the public surface.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then no diagnostic or edit is invented from the spread syntax.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source


@pytest.mark.parametrize("annotation", ["Daytona", "Wrapped[Daytona]"])
def test_reviews_spread_when_operand_already_has_client_evidence(
    annotation: str,
) -> None:
    # Given a proven or uncertain client operand, whose mapping shape is unsupported.
    source = (
        "from daytona import Daytona\n"
        f"def load(client: {annotation}):\n    merged = {{**client}}\n"
        "    for value in merged.values(): value.get('url')\n"
    )
    # When following evidence through the spread.
    result = migrate(source)
    # Then the unsupported lookup remains visible for manual review.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "annotation", ["tuple[Client, Daytona]", "tuple[Daytona, Client]"]
)
@pytest.mark.parametrize("binding", ["left", "right"])
@pytest.mark.parametrize("access", ["unpack", "subscript", "iterate"])
def test_reviews_tuple_when_selected_position_has_client_evidence(
    annotation: str, binding: str, access: str
) -> None:
    # Given distinct HTTPX and Daytona positions in a typed tuple.
    index = 0 if binding == "left" else 1
    body = {
        "unpack": f"left, right = pair\n    {binding}.get('url')",
        "subscript": f"pair[{index}].get('url')",
        "iterate": "for value in pair: value.get('url')",
    }[access]
    source = (
        "from httpx import Client\nfrom daytona import Daytona\n"
        f"def launch(pair: {annotation}):\n    {body}\n"
    )
    is_client = (index == 0) == annotation.startswith("tuple[Daytona")
    # When migrating the call on one selected position, or an uncertain iteration.
    result = migrate(source)
    # Then only a possible Daytona instance needs manual review, never HTTPX.
    assert [item["kind"] for item in result.items] == (
        ["fixme"] if is_client or access == "iterate" else []
    )
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "annotation", ["tuple[type[Daytona], Client]", "tuple[Client, str]"]
)
def test_preserves_iteration_when_tuple_has_no_client_instances(
    annotation: str,
) -> None:
    # Given heterogeneous types which do not include a Daytona instance.
    source = (
        "from httpx import Client\nfrom daytona import Daytona\n"
        f"def launch(pair: {annotation}):\n    for value in pair: value.get('url')\n"
    )
    # When inspecting a moved member on each iteration value.
    result = migrate(source)
    # Then class objects and unrelated instances are not client evidence.
    assert result.source == source
    assert result.items == ()
