# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective stores retain uncertainty rather than stale receiver proofs."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize("write", ['setattr(self, "c", d)', "self.c = d"])
@pytest.mark.parametrize("consumer", ["self.c.get('url')", "Owner().c.get('url')"])
def test_property_stays_unproven_when_setter_replaces_value(
    write: str, consumer: str
) -> None:
    # Given a project setter which stores an HTTP client instead of its argument.
    source = (
        "from daytona import Daytona\nimport httpx\nclass Owner:\n"
        "    @property\n    def c(self) -> httpx.Client: return self._c\n"
        "    @c.setter\n    def c(self, value): self._c = httpx.Client()\n"
        f"    def __init__(self):\n        d = Daytona()\n        {write}\n"
        + (f"        {consumer}\n" if consumer.startswith("self") else consumer + "\n")
    )
    # When migrating a read of the setter-controlled property.
    result = migrate(source)
    # Then the original call remains, with linked uncertainty requiring review.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "setup,write",
    [
        ("import builtins as b", 'b.setattr(self, "c", h)'),
        ("setter = setattr", 'setter(self, "c", h)'),
        ("from builtins import setattr as setter", 'setter(self, "c", h)'),
        ("setter = setattr\nsetter = unknown", 'setter(self, "c", h)'),
    ],
)
@pytest.mark.parametrize("loop", [False, True])
def test_old_proof_is_invalidated_when_reflective_callable_is_aliased(
    setup: str, write: str, loop: bool
) -> None:
    # Given an aliased write after a proven client, possibly on a later iteration.
    body = (
        f"    for _ in values:\n        self.c.get('url')\n        {write}\n"
        if loop
        else f"    {write}\n    self.c.get('url')\n"
    )
    source = (
        f"from daytona import Daytona\n{setup}\n"
        "def run(self, h, values):\n    self.c = Daytona()\n" + body
    )
    # When migrating the old proof's consumer.
    result = migrate(source)
    # Then an alias cannot preserve an automatic rewrite of the old client.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "write,consumer,proven",
    [
        ("setattr(self, key, d)", "self.c.create()", False),
        ("self.__dict__.update(other_dict)", "self.c.create()", True),
        ("obj = self\nvars(obj).update(c=d)", "self.c.create()", True),
        ("self.__dict__.update(pool=[d])", "self.pool[0].create()", True),
    ],
)
@pytest.mark.parametrize("scope", ["local", "class"])
def test_reports_or_routes_when_reflective_store_carries_client_evidence(
    write: str, consumer: str, proven: bool, scope: str
) -> None:
    # Given client provenance through each reported previously-silent store.
    statements = "d = Daytona()\nother_dict = {'c': d}\n" + write
    body = "\n".join("    " + line for line in statements.splitlines())
    source = "from daytona import Daytona\n"
    source += (
        f"def run(self, key):\n{body}\n    {consumer}\n"
        if scope == "local"
        else "class Owner:\n    def __init__(self, key):\n"
        + "\n".join("    " + line for line in body.splitlines())
        + f"\n    def run(self):\n        {consumer}\n"
    )
    # When migrating the linked consumer.
    result = migrate(source)
    # Then literal stores prove the consumer; computed keys require its review.
    if proven:
        assert result.source == source.replace(".create()", ".v0.create()")
        assert result.items == ()
    else:
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert [(item["kind"], item["line"]) for item in result.items] == [
            ("fixme", len(source.splitlines()))
        ]


def test_strict_cli_reports_when_computed_store_links_unknown_field(
    tmp_path: Path,
) -> None:
    # Given a real project with client evidence but an unresolvable field name.
    path = tmp_path / "app.py"
    source = (
        "from daytona import Daytona\ndef run(self, key):\n"
        "    setattr(self, key, Daytona())\n    self.c.create()\n"
    )
    path.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating through the actual strict command.
    status = cli.main([str(tmp_path), "--force", "--yes", "--strict"])
    # Then uncertainty is actionable without rewriting the unproven consumer.
    assert status == 1
    assert ast.dump(ast.parse(path.read_text())) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("loop", [False, True])
def test_all_fields_are_invalidated_when_setter_alias_target_is_unknown(
    loop: bool,
) -> None:
    # Given an opaque callable formerly bound to setattr which can mutate any field.
    body = (
        "    for _ in values:\n        self.backup.create()\n        setter(self, 'c', h)\n"
        if loop
        else "    setter(self, 'c', h)\n    self.backup.create()\n"
    )
    source = (
        "from daytona import Daytona\nsetter = setattr\nsetter = unknown\n"
        "def run(self, h, values):\n    self.backup = Daytona()\n" + body
    )
    # When the callable no longer resolves to the builtin.
    result = migrate(source)
    # Then a different field cannot retain an automatic client proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "write", ["self.c = Daytona()", "setattr(self, 'c', Daytona())"]
)
def test_property_stays_unproven_when_project_setter_is_inherited(write: str) -> None:
    # Given an inherited setter that never stores the caller's client value.
    source = (
        "from daytona import Daytona\nimport httpx\nclass Parent:\n"
        "    @property\n    def c(self): return httpx.Client()\n"
        "    @c.setter\n    def c(self, value): pass\n"
        f"class Child(Parent):\n    def run(self):\n        {write}\n"
        "        self.c.get('url')\n"
    )
    # When collecting writes in the subclass rather than the descriptor owner.
    result = migrate(source)
    # Then inherited descriptor behavior also prevents an automatic rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
