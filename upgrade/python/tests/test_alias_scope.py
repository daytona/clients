# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Aliases must never borrow type bindings from their importing module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "declaration",
    [
        'HttpClient: TypeAlias = "Client"',
        pytest.param(
            "type HttpClient = Client",
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695"),
        ),
    ],
)
@pytest.mark.parametrize("defining_module", ["httpx", "daytona"])
@pytest.mark.parametrize("import_style", ["direct", "module", "reexport"])
def test_uses_defining_scope_when_alias_is_imported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    declaration: str,
    defining_module: str,
    import_style: str,
) -> None:
    # Given conflicting Client bindings in the alias owner and its consumer.
    owner = "Daytona as Client" if defining_module == "daytona" else "Client"
    (tmp_path / "deps.py").write_text(
        f"from typing import TypeAlias\nfrom {defining_module} import {owner}\n{declaration}\n"
    )
    (tmp_path / "bridge.py").write_text("from deps import HttpClient\n")
    imports, annotation = {
        "direct": ("from deps import HttpClient", "HttpClient"),
        "module": ("import deps", "deps.HttpClient"),
        "reexport": ("from bridge import HttpClient", "HttpClient"),
    }[import_style]
    consumer = "httpx" if defining_module == "daytona" else "daytona"
    other = "Daytona as Client" if consumer == "daytona" else "Client"
    source = (
        f"from {consumer} import {other}\n{imports}\n"
        f"def launch(http: {annotation}): return http.get('url')\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating through the real strict CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then only an alias defined as a Daytona instance gains the v0 namespace.
    assert status == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == (
        source.replace("http.get(", "http.v0.get(")
        if defining_module == "daytona"
        else source
    )
