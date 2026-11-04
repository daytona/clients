# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reflective class writes preserve conservative field and constructor evidence."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize("key", ["not-valid.child", "not-valid", "", "café"])
@pytest.mark.parametrize(
    "write",
    [
        "setattr(Service, {key!r}, Other())",
        "Service.__dict__[{key!r}] = Other()",
        "Service.__dict__.update({{{key!r}: Other()}})",
    ],
)
def test_field_review_when_reflective_class_key_is_not_an_identifier(
    tmp_path: Path, key: str, write: str
) -> None:
    # Given an imported holder with two independent field proofs.
    (tmp_path / "service.py").write_text(
        "from daytona import Daytona\nclass Service:\n"
        "    café = Daytona()\n    peer = Daytona()\n"
    )
    (tmp_path / "mutator.py").write_text(
        "from service import Service\n" + write.format(key=key) + "\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    app = tmp_path / "app.py"
    source = (
        "from service import Service\nService().café.list()\nService().peer.list()\n"
    )
    app.write_text(source)

    # When indexing a reflective write before upgrading another importer.
    status = main([str(tmp_path), "--strict", "--force", "--yes"])

    # Then opaque keys fence both fields; a Unicode identifier fences only itself.
    expected = (
        source.replace("Service().peer.list()", "Service().peer.v0.list()")
        if key.isidentifier()
        else source
    )
    output = app.read_text()
    assert status == 1
    assert ast.dump(ast.parse(output)) == ast.dump(ast.parse(expected))
    assert output.count("# FIXME(daytona-upgrade):") == (1 if key.isidentifier() else 2)


@pytest.mark.parametrize("constructor", ["Daytona", "Custom"])
@pytest.mark.parametrize(
    "write",
    [
        "setattr({owner}, name, stub)",
        "{owner}.__dict__.update(mapping)",
        "vars({owner}).update(**kw)",
    ],
)
@pytest.mark.parametrize("member", ["create", "stop", "snapshot.list"])
def test_member_review_when_instance_is_created_after_opaque_constructor_write(
    constructor: str, write: str, member: str
) -> None:
    # Given an SDK constructor or a local subclass mutated before instantiation.
    source = "from daytona import Daytona\n"
    if constructor == "Custom":
        source += "class Custom(Daytona): pass\n"
    source += (
        write.format(owner=constructor)
        + f"\nclient = {constructor}()\nclient.{member}()\n"
    )

    # When upgrading a call whose member may have been replaced.
    result = migrate(source)

    # Then constructor identity survives, so dispatch gets review, not silence.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)
    assert "# FIXME(daytona-upgrade):" in result.source
