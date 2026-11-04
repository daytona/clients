# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Module type aliases carry client evidence through the real migration surface."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main

PEP695 = pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 needs 3.12")


@pytest.mark.parametrize(
    "alias",
    [
        "Dep = Daytona",
        "Dep = Annotated[Daytona, Depends(get_client)]",
        "Dep = Optional[Daytona]",
        "Dep = 'Daytona'",
        'Dep = "Annotated[Daytona, Depends(get_client)]"',
        "Dep = Optional['Daytona']",
        "Dep: TypeAlias = Annotated[Daytona, Depends(get_client)]",
        'Dep: TypeAlias = "Optional[Daytona]"',
        "First = Annotated[Daytona, Depends(get_client)]\nDep = First",
        "First = 'Second'\nSecond = 'Daytona'\nDep = First",
        "Dep = t.Annotated[Daytona, Depends(get_client)]",
        "Dep = A[Daytona, Depends(get_client)]",
        "Dep = Optional[Daytona]\nDaytona = unrelated",
        pytest.param(
            "type Dep = Annotated[Daytona, Depends(get_client)]", marks=PEP695
        ),
        pytest.param('type Dep = "Optional[Daytona]"', marks=PEP695),
        pytest.param(
            "type First = Second\ntype Second = Daytona\ntype Dep = First", marks=PEP695
        ),
    ],
)
@pytest.mark.parametrize("annotation", ["Dep", '"Dep"'])
def test_routes_client_when_module_alias_proves_annotation(
    alias: str, annotation: str
) -> None:
    # Given the FastAPI dependency pattern, including nullable and quoted aliases.
    source = (
        "from daytona import Daytona\nimport typing as t\n"
        "from typing import Annotated, Annotated as A, Optional, TypeAlias\n"
        "from fastapi import Depends\n"
        "def get_client() -> Daytona: return Daytona()\n"
        f"{alias}\ndef launch(d: {annotation}):\n    d.create()\n"
    )
    # When migrating the aliased parameter.
    result = migrate(source)
    # Then the call routes identically to an inline annotation, without manual work.
    assert result.source == source.replace("d.create()", "d.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "alias",
    [
        "Dep = Unknown[Daytona]",
        "Dep: TypeAlias = choose(Daytona)",
        "Dep = Daytona | str",
        "Dep = 'MissingDaytonaAlias'",
        "Dep = 'Annotated[Daytona'",
        "Dep = 'OtherDaytona'\nOtherDaytona = 'Dep'",
        "Dep = 'A0'\n"
        + "\n".join(f"A{i} = 'A{i + 1}'" for i in range(40))
        + "\nA40 = Daytona",
        pytest.param("type Dep = Unknown[Daytona]", marks=PEP695),
    ],
)
def test_strict_requires_review_when_alias_cannot_prove_client(
    alias: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an alias with client evidence that cannot safely establish its type.
    source = (
        "from daytona import Daytona\nfrom typing import TypeAlias\n"
        f"{alias}\ndef launch(d: Dep):\n    d.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When running the actual strict writing command.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then uncertainty cannot yield a clean report or an unsafe rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == 1, json.dumps(report)
    assert [item["kind"] for item in report["items"]] == ["fixme"]
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(source))


def test_strict_routes_client_when_fastapi_alias_is_proven(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given the recommended FastAPI dependency alias and an old SDK dependency.
    source = (
        "from typing import Annotated\nfrom fastapi import Depends\n"
        "from daytona import Daytona\n"
        "def get_client() -> Daytona: return Daytona()\n"
        "DaytonaDep = Annotated[Daytona, Depends(get_client)]\n"
        "def launch(d: DaytonaDep):\n    d.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the strict writing CLI upgrades the project.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then a successful command means the runtime call really moved to v0.
    assert status == 0
    assert json.loads(capsys.readouterr().out)["items"] == []
    assert app.read_text() == source.replace("d.create()", "d.v0.create()")


@pytest.mark.parametrize(
    "alias",
    [
        "Dep = Annotated[str, Daytona]",
        "Dep = str",
        "Dep = 'Unrelated'",
        "Dep = Annotated[Daytona, metadata]\nDep = str",
    ],
)
def test_preserves_nonclient_when_alias_has_no_client_type(alias: str) -> None:
    # Given unrelated types, metadata, or an explicitly rebound alias.
    source = (
        "from daytona import Daytona\nfrom typing import Annotated\n"
        + alias
        + "\ndef run(d: Dep): d.create()\n"
    )
    # When analyzing the parameter.
    result = migrate(source)
    # Then metadata and obsolete alias bindings never prove the receiver.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "use",
    [
        "d: Dep = get_client()\nd.create()",
        "def factory() -> Dep: return get_client()\nfactory().create()",
        "class Worker:\n    d: Dep\n    def run(self): self.d.create()",
    ],
)
def test_routes_client_when_alias_is_used_outside_parameter(use: str) -> None:
    # Given the same aliased client type at each annotation consumer.
    source = (
        "from daytona import Daytona\nfrom typing import Optional\nDep = Optional[Daytona]\n"
        + use
        + "\n"
    )
    # When migrating assignments, return values, or class attributes.
    result = migrate(source)
    # Then all consumers share alias resolution instead of special-casing parameters.
    assert result.source == source.replace(".create()", ".v0.create()")
    assert result.items == ()
