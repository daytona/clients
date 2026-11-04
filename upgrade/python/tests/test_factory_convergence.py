# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Factory links reach review regardless of function count or module layout."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "layout",
    [(length, modules) for length in (4, 10, 30) for modules in (1, 2, length)],
)
def test_requires_review_when_factory_chain_exceeds_module_count(
    layout: tuple[int, int], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given forward factory links, including multiple links inside each module.
    length, modules = layout
    sources: dict[str, str] = {}
    for module in range(modules):
        start, end = module * length // modules, (module + 1) * length // modules
        source = (
            f"from provider{module + 1:02} import f{end}\n"
            if module + 1 < modules
            else "from daytona import Daytona\n"
        )
        source += "".join(
            f"def f{number}():\n    return "
            + (f"f{number + 1}()" if number + 1 < length else "Daytona()")
            + "\n"
            for number in range(start, end)
        )
        sources[f"provider{module:02}.py"] = source
    consumer = (
        sources.pop("provider00.py") if modules == 1 else "from provider00 import f0\n"
    ) + "client = f0()\nclient.create()\n"
    sources["app.py"] = consumer
    for name, source in sources.items():
        (tmp_path / name).write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When the real CLI indexes all functions before strict migration.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then the linked call needs review, never a silent success or guessed rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [(item["kind"], item["file"]) for item in report["items"]] == [
        ("fixme", "app.py")
    ]
    assert "# FIXME(daytona-upgrade):" in (tmp_path / "app.py").read_text()
    assert ast.dump(ast.parse((tmp_path / "app.py").read_text())) == ast.dump(
        ast.parse(consumer)
    )


@pytest.mark.parametrize("linked", [False, True])
def test_terminates_when_factories_form_a_cycle(
    linked: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given recursive summaries, optionally linked to an SDK return alternative.
    source = (
        "from daytona import Daytona\n"
        "def f0():\n    return f1()\n"
        "def f1():\n    return "
        + ("Daytona() if flag else f0()" if linked else "f0()")
        + "\nclient = f0()\nclient.create()\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")

    # When recursive evidence is indexed to convergence.
    status = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])

    # Then only the linked cycle needs review, and neither cycle proves a rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == int(linked)
    assert [item["kind"] for item in report["items"]] == (["fixme"] if linked else [])
    assert ast.dump(ast.parse((tmp_path / "app.py").read_text())) == ast.dump(
        ast.parse(source)
    )
