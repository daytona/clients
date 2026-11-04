# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regressions for intermediate with stores, shell comments, and UTF-8 BOMs."""

from __future__ import annotations

import codecs
import json
from pathlib import Path

import pytest
from test_flow import migrate
from test_notebook_environment import notebook_with_magic

from daytona_upgrade.cli import main


@pytest.mark.parametrize("context", ["with suppress(E)", "async with manager()"])
@pytest.mark.parametrize(
    "body",
    [
        "client = Other()\nrisky()\nclient = Daytona()",
        "if flag:\n    client = Other()\n    risky()\nclient = Daytona()",
        "with manager():\n    client = Other()\n    risky()\nclient = Daytona()",
        "client: Other = Other()\nrisky()\nclient = Daytona()",
        "(client := Other())\nrisky()\nclient = Daytona()",
        "client, value = Other(), 1\nrisky()\nclient = Daytona()",
        "del client\nrisky()\nclient = Daytona()",
        "from other import client\nrisky()\nclient = Daytona()",
    ],
)
def test_unproven_when_context_body_restores_intermediate_store(
    context: str, body: str
) -> None:
    # Given a client restored only after a potentially suppressed exception.
    statements = "\n".join("        " + line for line in body.splitlines())
    source = (
        "from daytona import Daytona\nfrom contextlib import suppress\n"
        "async def run():\n    client = Daytona()\n"
        f"    {context}:\n{statements}\n    client.create()\n"
    )
    # When migrating the receiver after the suppressing context.
    result = migrate(source)
    # Then the possible intermediate binding is left for manual review.
    assert result.source.splitlines()[-1].strip() == "client.create()"
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("context", ["with manager()", "async with manager()"])
def test_proven_when_every_nested_context_write_is_client(context: str) -> None:
    # Given only client writes, with a separate untouched client binding.
    source = (
        "from daytona import Daytona\nasync def run():\n"
        "    client = Daytona()\n    untouched = Daytona()\n"
        f"    {context}:\n        if flag:\n            client = Daytona()\n"
        "        with manager():\n            client = Daytona()\n"
        "    client.create()\n    untouched.create()\n"
    )
    # When joining all potentially suppressed prefixes.
    result = migrate(source)
    # Then every possible receiver still has a client proof.
    assert "client.v0.create()" in result.source
    assert "untouched.v0.create()" in result.source


@pytest.mark.parametrize(
    "value",
    [
        "https://custom # comment",
        "'https://custom' # comment",
        '"https://custom#part" # comment',
    ],
)
def test_refusal_when_shell_export_has_trailing_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    # Given a custom shell export whose trailing tokens are a comment.
    source = notebook_with_magic("!export DAYTONA_API_URL=" + value)
    notebook = tmp_path / "example.ipynb"
    notebook.write_text(source)
    # When migrating through the real writing CLI.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the custom URL refuses the migration without changing the notebook.
    report = json.loads(capsys.readouterr().out)
    assert status == 2
    assert any(item["kind"] == "custom-api-url" for item in report["items"])
    assert report["changedFiles"] == []
    assert notebook.read_text() == source


def test_migrates_when_notebook_has_utf8_bom(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a UTF-8 BOM notebook containing a migratable client call.
    notebook = tmp_path / "example.ipynb"
    source = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["from daytona import Daytona\n", "Daytona().create()\n"],
                }
            ]
        }
    )
    notebook.write_bytes(codecs.BOM_UTF8 + source.encode("utf-8"))
    # When migrating and writing the notebook.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then the migrated call and the original BOM both reach disk.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["changedFiles"] == ["example.ipynb"]
    output = notebook.read_bytes()
    assert output.startswith(codecs.BOM_UTF8)
    migrated = json.loads(output.decode("utf-8-sig"))
    assert "Daytona().v0.create()" in "".join(migrated["cells"][0]["source"])


@pytest.mark.parametrize("name", ["broken.py", "broken.pyi"])
def test_convert_guidance_when_bom_declares_invalid_utf8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    # Given a BOM declaration followed by invalid UTF-8 on a later source line.
    data = (
        codecs.BOM_UTF8 + b"# first\n# second\n# caf\xe9\nfrom daytona import Sandbox\n"
    )
    path = tmp_path / name
    path.write_bytes(data)
    # When the CLI diagnoses the input encoding.
    status = main([str(tmp_path), "--json", "--force", "--yes"])
    # Then it prescribes conversion, not adding a conflicting coding cookie.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert report["items"][0]["message"] == (
        f"{name} is not valid UTF-8; convert it to UTF-8, then rerun."
    )
    assert path.read_bytes() == data
