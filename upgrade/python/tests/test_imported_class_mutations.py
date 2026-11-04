# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Imported class field proofs observe writes throughout the indexed project."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "mutation",
    [
        "Service.client = Other()",
        'setattr(Service, "client", Other())',
        "del Service.client",
        "Alias = Service\nAlias.client = Other()",
        'Alias = Service\nsetattr(Alias, "client", Other())',
        "Alias = Service\ndel Alias.client",
        "setattr(Service, field_name, Other())",
        "Service.__dict__.update(updates)",
        "Service.unrelated = Other()",
        'Service.__dict__["client"]',
    ],
)
@pytest.mark.parametrize(
    "scenario", [(False, False), (False, True), (True, False), (True, True)]
)
def test_field_proof_is_reviewed_when_imported_owner_is_mutated(
    tmp_path: Path, mutation: str, scenario: tuple[bool, bool]
) -> None:
    # Given an imported class whose client fields have canonical proof metadata.
    third_module, factory = scenario
    definition = (
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Service:\n"
        "    client: Daytona = field(default_factory=Daytona)\n"
        "    peer: Daytona = field(default_factory=Daytona)\n"
        if factory
        else "from daytona import Daytona\nclass Service:\n"
        "    client = Daytona()\n    peer = Daytona()\n"
    )
    (tmp_path / "service.py").write_text(definition)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    app = tmp_path / "app.py"
    source = "from service import Service\n"
    if third_module:
        (tmp_path / "mutator.py").write_text(source + mutation + "\n")
    else:
        source += mutation + "\n"
    source += "Service().client.list()\nService().peer.list()\n"
    app.write_text(source)
    control = (
        mutation.startswith("Service.unrelated")
        or mutation == 'Service.__dict__["client"]'
    )
    opaque = not control and ("field_name" in mutation or "__dict__" in mutation)

    # When the real CLI indexes all modules before rewriting the importing module.
    status = main([str(tmp_path), "--strict", "--force", "--yes"])

    # Then linked writes get review, while untouched fields retain their proofs.
    expected = source
    if control:
        expected = expected.replace(
            "Service().client.list()", "Service().client.v0.list()"
        )
    if not opaque:
        expected = expected.replace("Service().peer.list()", "Service().peer.v0.list()")
    assert status == (0 if control else 1)
    output = app.read_text()
    assert ast.dump(ast.parse(output)) == ast.dump(ast.parse(expected))
    assert output.count("# FIXME(daytona-upgrade):") == (
        0 if control else 2 if opaque else 1
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "import service as mod\nmod.Service.client = Other()\n",
        'import service\nsetattr(service.Service, "client", Other())\n',
        "from bridge import Imported\nAlias = Imported\ndel Alias.client\n",
        "def patch():\n    from service import Service as Local\n"
        "    Local.client = Other()\n",
        "from service import Service\nAlias = Service\n"
        "setattr(Alias, field_name, Other())\n",
    ],
)
def test_owner_fence_reaches_other_importers_when_write_uses_qualified_or_scoped_alias(
    tmp_path: Path, mutation: str
) -> None:
    # Given a class re-export and a mutation outside its defining and consuming files.
    (tmp_path / "service.py").write_text(
        "from daytona import Daytona\nclass Service:\n    client = Daytona()\n"
        "def use_default():\n    Service().client.list()\n"
    )
    (tmp_path / "bridge.py").write_text("from service import Service as Imported\n")
    (tmp_path / "mutator.py").write_text(mutation)
    app = tmp_path / "app.py"
    source = "from bridge import Imported\nImported().client.list()\n"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When the CLI resolves canonical owners across imports and function scopes.
    status = main([str(tmp_path), "--strict", "--force", "--yes"])

    # Then neither an importing module nor the defining module trusts the default.
    assert status == 1
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
    assert "# FIXME(daytona-upgrade):" in app.read_text()
    default_output = (tmp_path / "service.py").read_text()
    assert "Service().client.list()" in default_output
    assert "# FIXME(daytona-upgrade):" in default_output
