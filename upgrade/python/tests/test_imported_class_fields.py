# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Imported constructors retain field proofs before instance exports demote them."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "binding",
    [
        "from service import Service",
        "from service import Service as ImportedService",
        "from service import *",
        "import service",
    ],
)
@pytest.mark.parametrize(
    ("definition", "arguments", "proven"),
    [
        ("    client: Daytona = field(default_factory=Daytona)\n", "", True),
        ("    client = Daytona()\n", "", True),
        ("    client: Daytona\n", "Daytona()", True),
        ("    def __init__(self): self.client = Daytona()\n", "", False),
        (
            "    client: Daytona = field(default_factory=Daytona)\n"
            "    def reset(self): self.client = unknown\n",
            "",
            False,
        ),
        (
            "    client: Daytona = field(default_factory=Daytona)\n"
            "    def __getattribute__(self, name): return unknown\n",
            "",
            False,
        ),
        (
            "    client: Daytona = field(default_factory=Daytona)\n"
            "    def __setattr__(self, name, value): pass\n",
            "",
            False,
        ),
        (
            "    client: Daytona = unknown\n"
            "    def __init__(self, client): self.client = transform(client)\n",
            "Daytona()",
            False,
        ),
        (
            "    client: Daytona = field(default_factory=Daytona)\n"
            "Service.__init__ = replacement\n",
            "",
            False,
        ),
    ],
    ids=[
        "factory",
        "class-default",
        "argument",
        "instance-store",
        "unproven",
        "read-hook",
        "write-hook",
        "custom-init",
        "replaced-init",
    ],
)
def test_fields_keep_evidence_when_imported_class_is_instantiated(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    binding: str,
    definition: str,
    arguments: str,
    proven: bool,
) -> None:
    # Given a class imported into app, then an instance imported into consumer.
    (tmp_path / "service.py").write_text(
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Service:\n" + definition
    )
    constructor = (
        "service.Service"
        if binding == "import service"
        else "ImportedService"
        if " as " in binding
        else "Service"
    )
    app = tmp_path / "app.py"
    source = (
        f"{binding}\nfrom daytona import Daytona\n"
        f"svc = {constructor}({arguments})\nsvc.client.list()\n"
    )
    app.write_text(source)
    consumer = tmp_path / "consumer.py"
    consumer_source = "from app import svc\nsvc.client.list()\n"
    consumer.write_text(consumer_source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When upgrading the whole three-module chain through the strict CLI.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])

    # Then construction uses local-class proofs; exported instances stay review-only.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["file"], item["kind"]) for item in report["items"]] == (
        ([] if proven else [("app.py", "fixme")]) + [("consumer.py", "fixme")]
    )
    expected = (
        source.replace("svc.client.list()", "svc.client.v0.list()")
        if proven
        else source
    )
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(expected))
    assert ast.dump(ast.parse(consumer.read_text())) == ast.dump(
        ast.parse(consumer_source)
    )
    assert "# FIXME(daytona-upgrade):" in consumer.read_text()


def test_imported_class_fields_keep_qualified_owner_when_local_class_has_same_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a linked imported class and an unrelated local class with the same name.
    (tmp_path / "service.py").write_text(
        "from dataclasses import dataclass, field\nfrom daytona import Daytona\n"
        "@dataclass\nclass Service:\n    client: Daytona = field(default_factory=Daytona)\n"
    )
    app = tmp_path / "app.py"
    source = (
        "from service import Service as ImportedService\n"
        "class Service:\n    client = []\n"
        "svc = ImportedService()\nlocal = Service()\n"
        "svc.client.list()\nlocal.client.list()\n"
    )
    app.write_text(source)
    consumer = tmp_path / "consumer.py"
    consumer_source = (
        "from app import svc, local\nsvc.client.list()\nlocal.client.list()\n"
    )
    consumer.write_text(consumer_source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When migrating both owners in the same importing module.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])

    # Then the imported proof neither disappears nor attaches to the local class.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["file"], item["kind"]) for item in report["items"]] == [
        ("consumer.py", "fixme")
    ]
    assert app.read_text() == source.replace(
        "svc.client.list()", "svc.client.v0.list()"
    )
    assert ast.dump(ast.parse(consumer.read_text())) == ast.dump(
        ast.parse(consumer_source)
    )
