# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective writes participate in sequential and loop receiver proofs."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest
from test_flow import migrate

from daytona_upgrade import cli
from daytona_upgrade.store_names import stored_names

WRITES: Final = (
    'setattr(self, "c", VALUE)',
    'self.__dict__["c"] = VALUE',
    "vars(self).update(c=VALUE)",
    'vars(self)["c"] = VALUE',
    'object.__setattr__(self, "c", VALUE)',
    "self.__dict__.update(c=VALUE)",
)


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize("loop", [False, True])
@pytest.mark.parametrize("external", ["httpx.Client()", "requests.Session()"])
def test_external_call_stays_unproven_when_reflective_write_replaces_client(
    write: str, loop: bool, external: str
) -> None:
    # Given a prior client and a real HTTP receiver on a later iteration/use.
    write = write.replace("VALUE", "h")
    body = (
        f'    for _ in range(2):\n        self.c.get("url")\n        {write}\n'
        if loop
        else f'    {write}\n    self.c.get("url")\n'
    )
    source = (
        "from daytona import Daytona\nimport httpx, requests\n"
        f"def run(self):\n    h = {external}\n    self.c = Daytona()\n" + body
    )
    # When migrating a call which may no longer target Daytona.
    result = migrate(source)
    # Then no external receiver is redirected to v0.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    if loop:
        assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize("loop", [False, True])
def test_client_call_migrates_when_literal_reflective_write_assigns_client(
    write: str, loop: bool
) -> None:
    # Given a literal attribute name and a known client value at the store.
    write = write.replace("VALUE", "Daytona()")
    body = f"    {write}\n    self.c.create()\n"
    if loop:
        body = "    for _ in range(2):\n" + "".join(
            "    " + line for line in body.splitlines(keepends=True)
        )
    source = "from daytona import Daytona\ndef run(self):\n" + body
    # When inferring the written field rather than only killing it.
    result = migrate(source)
    # Then the proven client migrates without manual review.
    assert "self.c.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize("write", WRITES)
def test_class_client_migrates_when_reflective_initializer_assigns_client(
    write: str,
) -> None:
    # Given initialization in another method, including dictionary updates.
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self):\n        "
        + write.replace("VALUE", "Daytona()")
        + "\n    def run(self):\n        self.c.create()\n"
    )
    # When collecting class-wide initialization evidence.
    result = migrate(source)
    # Then the indirect write proves the same attribute as a direct assignment.
    assert "self.c.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize(
    "write",
    [
        "setattr(self, key, h)",
        "object.__setattr__(self, key, h)",
        "self.__dict__[key] = h",
        "vars(self)[key] = h",
        "vars(self).update(**fields)",
        "self.__dict__.update(**fields)",
        "vars(self).update({key: h})",
        "self.__dict__.update(fields)",
    ],
)
@pytest.mark.parametrize("loop", [False, True])
def test_attribute_proofs_are_fenced_when_write_keys_are_unknown(
    write: str, loop: bool
) -> None:
    # Given multiple client attributes and a write that may target either.
    body = (
        f"    for _ in range(2):\n        self.c.create()\n        self.backup.create()\n        {write}\n"
        if loop
        else f"    {write}\n    self.c.create()\n    self.backup.create()\n"
    )
    source = (
        "from daytona import Daytona\ndef run(self, key, fields, h):\n"
        "    self.c = Daytona()\n    self.backup = Daytona()\n" + body
    )
    # When the key or keyword expansion cannot be resolved statically.
    result = migrate(source)
    # Then every potentially overwritten field loses its automatic proof.
    assert ".v0.create()" not in result.source
    if loop:
        assert len(result.items) == 2


@pytest.mark.parametrize("write", WRITES)
def test_loop_write_summary_names_literal_reflective_attribute(write: str) -> None:
    # Given the syntax consumed by the loop backedge summary.
    node = ast.parse(write.replace("VALUE", "h"))
    # When collecting stores without evaluating values.
    names = stored_names(node)
    # Then the precise field participates in the backedge join.
    assert "self.c" in names
    assert "self.*" not in names


@pytest.mark.parametrize("write", WRITES)
def test_strict_cli_requires_review_when_loop_replaces_client(
    tmp_path: Path, write: str
) -> None:
    # Given an actual project containing the reported loop backedge.
    path = tmp_path / "app.py"
    source = (
        "from daytona import Daytona\nimport httpx\n"
        "def run(self):\n    self.c = Daytona()\n    h = httpx.Client()\n"
        "    for _ in range(2):\n        self.c.get('url')\n        "
        + write.replace("VALUE", "h")
        + "\n"
    )
    path.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When upgrading through the user-facing strict CLI.
    status = cli.main([str(tmp_path), "--force", "--yes", "--strict"])
    # Then unresolved receiver identity is reported instead of silently rewritten.
    assert status == 1
    assert ast.dump(ast.parse(path.read_text())) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "shadow,write",
    [
        ("setattr = custom", 'setattr(self, "c", Daytona())'),
        ("vars = custom", 'vars(self)["c"] = Daytona()'),
        ("object = custom", 'object.__setattr__(self, "c", Daytona())'),
        ("builtins = custom", 'builtins.setattr(self, "c", Daytona())'),
    ],
)
def test_client_stays_unproven_when_reflective_builtin_is_shadowed(
    shadow: str, write: str
) -> None:
    # Given a project callable whose spelling cannot certify a real field store.
    source = (
        "from daytona import Daytona\ndef run(self):\n"
        f"    {shadow}\n    {write}\n    self.c.create()\n"
    )
    # When considering literal-key reflective value evidence.
    result = migrate(source)
    # Then a shadowed operation never manufactures a client proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize("loop", [False, True])
def test_client_proof_survives_when_literal_write_targets_sibling(
    write: str, loop: bool
) -> None:
    # Given a known client and an indirect store to a different field.
    write = (
        write.replace('"c"', '"other"').replace("c=", "other=").replace("VALUE", "h")
    )
    body = f"    {write}\n    self.c.create()\n"
    if loop:
        body = "    for _ in values:\n" + "".join(
            "    " + line for line in body.splitlines(keepends=True)
        )
    source = (
        "from daytona import Daytona\ndef run(self, h, values):\n"
        "    self.c = Daytona()\n" + body
    )
    # When collecting precise attribute destinations.
    result = migrate(source)
    # Then unrelated literal stores do not cause broad invalidation or FIXMEs.
    assert result.source == source.replace("self.c.create()", "self.c.v0.create()")
    assert not result.items


@pytest.mark.parametrize("write", WRITES)
def test_deferred_call_stays_unproven_when_later_store_replaces_client(
    write: str,
) -> None:
    # Given a closure which may execute after the enclosing reflective store.
    source = (
        "from daytona import Daytona\nimport httpx\nobj.c = Daytona()\n"
        "def run():\n    obj.c.get('url')\n"
        + write.replace("self", "obj").replace("VALUE", "httpx.Client()")
        + "\nrun()\n"
    )
    # When considering future enclosing stores before proving the closure call.
    result = migrate(source)
    # Then an old local proof cannot certify a later external receiver.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
