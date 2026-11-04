# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Computed export membership does not erase concrete unrelated provenance."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("chain", [False, True])
@pytest.mark.parametrize(
    "binding,uncertain",
    [
        ('CONFIG = {"key": 1}', False),
        ('CONFIG = ["key"]', False),
        ("CONFIG = []", False),
        ("CONFIG = [unknown, 1]", False),
        ("CONFIG = {'key': unknown}", False),
        ('CONFIG = "key"', False),
        ("CONFIG = 1", False),
        ("from httpx import Client\nCONFIG = Client()", False),
        ("from httpx import Client as CONFIG", False),
        ("CONFIG = unknown", True),
        ("from daytona import Daytona\nCONFIG = Daytona()", True),
    ],
)
def test_retains_defining_evidence_when_export_membership_is_computed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    chain: bool,
    binding: str,
    uncertain: bool,
) -> None:
    # Given ordinary settings or an unknown/client binding behind computed exports.
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    declaration = "__all__ = [n for n in dir() if not n.startswith('_')]\n"
    (package / "settings.py").write_text(binding + "\n" + declaration)
    module = "settings"
    if chain:
        (package / "bridge.py").write_text("from .settings import *\n" + declaration)
        module = "bridge"
    source = f"from .{module} import *\n" + "".join(
        f'def read_{index}(): return CONFIG.get("key")\n' for index in range(25)
    )
    app = package / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating the actual project surface.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then concrete non-clients stay clean; unknown or client exports need review.
    report = json.loads(capsys.readouterr().out)
    items = [item for item in report["items"] if item["file"] == "pkg/app.py"]
    assert status == int(uncertain)
    assert [item["kind"] for item in items] == ["fixme"] * (25 if uncertain else 0)
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))


def test_reviews_elements_when_computed_import_may_leave_unrelated_collection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a client collection that might not replace the existing HTTPX list.
    (tmp_path / "settings.py").write_text(
        "from daytona import Daytona\nCONFIG = [Daytona()]\n"
        "__all__ = list(('CONFIG',))\n"
    )
    source = (
        "from httpx import Client\nCONFIG = [Client()]\n"
        "from settings import *\nCONFIG[0].get('url')\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When upgrading the uncertain wildcard replacement.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then container provenance cannot certify the element's client identity.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["fixme"]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
