# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Computed wildcard surfaces retain uncertainty across re-export chains."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("incoming", [False, True])
@pytest.mark.parametrize("usage", ["annotation", "constructor", "instance"])
def test_reviews_receiver_when_any_wildcard_surface_is_computed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    depth: int,
    incoming: bool,
    usage: str,
) -> None:
    # Given a computed surface after literal star re-exports, with either incoming
    # Daytona evidence or a local Daytona proof that HTTPX could replace.
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    origin = (
        "from daytona import Daytona as C"
        if incoming
        else "from httpx import Client as C"
    )
    instance = "\nvalue = C()" if usage == "instance" else ""
    names = "['C', 'value']" if usage == "instance" else "['C']"
    (package / "leaf.py").write_text(origin + instance + f"\n__all__ = {names}\n")
    previous = "leaf"
    for index in range(depth):
        module = f"bridge{index}"
        declaration = (
            "__all__ = [n for n in dir() if not n.startswith('_')]"
            if index == depth - 1
            else f"__all__ = {names}"
        )
        (package / f"{module}.py").write_text(
            f"from .{previous} import *\n{declaration}\n"
        )
        previous = module
    local = "" if incoming else "from daytona import Daytona as C\nvalue = C()\n"
    call = {
        "annotation": "def launch(x: C): return x.get('url')",
        "constructor": "C().get('url')",
        "instance": "value.get('url')",
    }[usage]
    source = local + f"from .{previous} import *\n" + call + "\n"
    app = package / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating through the public CLI rather than a prebuilt index.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then uncertain membership neither silently loses evidence nor rewrites HTTPX.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [
        item["kind"] for item in report["items"] if item["file"] == "pkg/app.py"
    ] == ["fixme"]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))


def test_reviews_receiver_when_computed_surface_is_inside_chain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given uncertainty upstream of two otherwise literal re-export surfaces.
    (tmp_path / "leaf.py").write_text(
        "from daytona import Daytona as C\n__all__ = list(('C',))\n"
    )
    (tmp_path / "middle.py").write_text("from leaf import *\n__all__ = ['C']\n")
    (tmp_path / "outer.py").write_text("from middle import *\n")
    source = "from outer import *\ndef launch(x: C): x.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When importing through the full chain.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then downstream literal declarations cannot restore a lost receiver proof.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"] if item["file"] == "app.py"] == [
        "fixme"
    ]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))
