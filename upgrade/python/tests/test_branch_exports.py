# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Module and branch boundaries preserve only justified receiver evidence."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main

PEP695 = pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695")


@pytest.mark.parametrize("exports", ["[]", "['other']", "('other',)"])
@pytest.mark.parametrize("import_style", ["star", "explicit"])
# Two CLI fixtures plus independent import/membership axes preserve useful pytest IDs.
def test_honors_all_when_wildcard_would_override_local_binding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], exports: str, import_style: str
) -> None:
    # Given an excluded Daytona alias and a local HTTPX binding of the same name.
    (tmp_path / "deps.py").write_text(
        "from daytona import Daytona as C\nother = 1\n" + f"__all__ = {exports}\n"
    )
    source = (
        "from httpx import Client as C\n"
        f"from deps import {'*' if import_style == 'star' else 'C'}\n"
        "def launch(x: C): return x.get('url')\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating the consumer through the real CLI.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then __all__ restricts star imports, never explicit imports.
    assert status == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == (
        source if import_style == "star" else source.replace("x.get(", "x.v0.get(")
    )


@pytest.mark.parametrize(
    "declaration",
    ["__all__ = ['_C']", "__all__ = []\n__all__ += ['_C']", "__all__ = () + ('_C',)"],
)
def test_imports_private_alias_when_explicitly_in_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], declaration: str
) -> None:
    # Given a private alias explicitly exposed by the defining module.
    (tmp_path / "deps.py").write_text(
        "from daytona import Daytona as _C\n" + declaration + "\n"
    )
    source = "from deps import *\ndef launch(x: _C): x.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When importing its public surface through a star import.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then __all__ also permits names which the default underscore rule excludes.
    assert status == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source.replace("x.create()", "x.v0.create()")


def test_preserves_constructor_when_import_only_exists_for_type_checking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an imported annotation alias which does not provide a runtime constructor.
    (tmp_path / "deps.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from daytona import Daytona as C\n"
    )
    source = "from deps import C\nx = C()\nx.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When inspecting a runtime construction rather than an annotation.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then static-only evidence never certifies a runtime client call.
    assert status == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source


@pytest.mark.parametrize(
    "declaration",
    [
        "from daytona import Daytona as C",
        'C: TypeAlias = "Daytona"',
        pytest.param("type C = Daytona", marks=PEP695),
    ],
)
@pytest.mark.parametrize("import_style", ["direct", "module", "reexport"])
def test_exports_alias_when_declared_under_type_checking(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    declaration: str,
    import_style: str,
) -> None:
    # Given an annotation alias that exists only for static typing in its owner.
    (tmp_path / "deps.py").write_text(
        "from typing import TYPE_CHECKING, TypeAlias\n"
        "if TYPE_CHECKING:\n    from daytona import Daytona\n"
        f"    {declaration}\n"
    )
    (tmp_path / "bridge.py").write_text("from deps import C\n")
    imports, annotation = {
        "direct": ("from deps import C", "C"),
        "module": ("import deps", "deps.C"),
        "reexport": ("from bridge import C", "C"),
    }[import_style]
    source = f"{imports}\ndef launch(x: {annotation}): x.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When migrating a consumer's annotated instance.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then the alias is resolved in its defining typing scope across every hop.
    assert status == 0, capsys.readouterr().out
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source.replace("x.create()", "x.v0.create()")


@pytest.mark.parametrize(
    "branches",
    [
        ("from daytona import Daytona as C", "from httpx import Client as C"),
        pytest.param(("type C = Daytona", "type C = str"), marks=PEP695),
        ("C: TypeAlias = Daytona", "C: TypeAlias = str"),
        ("C = Daytona", "C = str"),
        ("C = Daytona()", "C = Client()"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_reviews_receiver_when_branches_conflict(
    branches: tuple[str, str], reverse: bool
) -> None:
    # Given incompatible client types or values at a branch join.
    left, right = reversed(branches) if reverse else branches
    runtime = "()" in left or "()" in right
    call = "C.get('url')" if runtime else "def launch(x: C): x.get('url')"
    source = (
        "from daytona import Daytona, AsyncDaytona\nfrom httpx import Client\n"
        "from typing import TypeAlias\n"
        f"if condition:\n    {left}\nelse:\n    {right}\n{call}\n"
    )
    # When inspecting the call after the conflicting binding.
    result = migrate(source)
    # Then evidence is retained as a FIXME, never silently dropped or redirected.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
