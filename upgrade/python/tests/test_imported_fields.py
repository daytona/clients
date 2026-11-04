# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Imported holder fields carry review evidence, not receiver authority."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "consumer",
    [
        "from service import svc\nsvc.client.list()\n",
        "import service\nservice.svc.client.list()\n",
        "from service import svc as other\nother.client.list()\n",
        "from service import svc\nalias = svc\nalias.client.list()\n",
        "from service import *\nsvc.client.list()\n",
        "from service import svc\nfrom unittest.mock import patch\npatch.object(svc.client, 'list')\n",
    ],
)
def test_strict_reports_linked_field_when_holder_is_imported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], consumer: str
) -> None:
    # Given an imported instance whose client was initialized in its own module.
    (tmp_path / "service.py").write_text(
        "from daytona import Daytona\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\nsvc = Service()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(consumer)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When upgrading the consumer through the strict CLI.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])
    # Then cross-module mutable fields are reported without an unproven rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    expected = "mock-target" if "patch.object" in consumer else "fixme"
    assert [(item["file"], item["kind"]) for item in report["items"]] == [
        ("app.py", expected)
    ]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(consumer))
    if expected == "fixme":
        assert "# FIXME(daytona-upgrade):" in app.read_text()


def test_unrelated_imported_field_stays_untouched_when_class_names_collide(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given unrelated same-named classes in different defining modules.
    (tmp_path / "service.py").write_text(
        "from daytona import Daytona\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\nsvc = Service()\n"
    )
    (tmp_path / "other.py").write_text(
        "class Service:\n    def __init__(self): self.client = []\nsvc = Service()\n"
    )
    app = tmp_path / "app.py"
    source = "from other import svc\nsvc.client.list()\n"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating a consumer of the unrelated instance.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])
    # Then class spelling alone cannot link a field to Daytona.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source


def test_imported_holder_requires_review_when_consumer_replaces_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given linked provenance followed by an opaque consumer-side replacement.
    (tmp_path / "service.py").write_text(
        "from daytona import Daytona\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\nsvc = Service()\n"
    )
    app = tmp_path / "app.py"
    source = "from service import svc\nsvc.client = opaque()\nsvc.client.list()\n"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the field is no longer provably the original client.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])
    # Then the tool keeps the call intact and reports its linked provenance.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["fixme"]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
