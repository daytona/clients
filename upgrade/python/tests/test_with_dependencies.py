# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression witnesses for suppressing contexts and direct-URL punctuation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from test_flow import migrate

from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "context",
    ["with suppress(FileNotFoundError)", "with unknown()", "async with unknown()"],
)
@pytest.mark.parametrize(
    "before,after", [("{}", "Daytona()"), ("Daytona()", "{}"), ("unknown", "Daytona()")]
)
def test_unproven_when_context_can_suppress_assignment(
    context: str, before: str, after: str
) -> None:
    # Given a context that may suppress an exception before the final assignment.
    source = f"from daytona import Daytona\nfrom contextlib import suppress\nasync def run():\n    client = {before}\n    {context}:\n        open('x')\n        client = {after}\n    client.get('k')\n"
    # When migrating its final receiver.
    result = migrate(source)
    # Then an uncertain binding is never silently namespaced.
    assert result.source.splitlines()[-1].strip() == "client.get('k')"
    assert any(item["kind"] == "fixme" for item in result.items)


def test_proven_when_client_agrees_before_and_after_context() -> None:
    # Given a client on both the suppressed and normal completion paths.
    source = "from daytona import Daytona\nclient = Daytona()\nwith unknown():\n    client = Daytona()\nclient.get('k')\n"
    # When migrating the receiver after the context.
    result = migrate(source)
    # Then a shared proof survives the join.
    assert "client.v0.get('k')" in result.source


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        " ; python_version >= '3.10'",
        " # mirror",
        " ; python_version >= '3.10' # mirror",
    ],
)
def test_valid_requirement_when_direct_url_contains_semicolon(suffix: str) -> None:
    # Given a URL semicolon, with an optional real marker/comment.
    source = (
        "daytona @ https://packages.example/download;build=123/daytona-0.100.0-py3-none-any.whl"
        + suffix
        + "\n"
    )
    # When migrating the parsed dependency.
    result = inspect_manifest(Path("requirements.txt"), source, load_mapping())
    # Then URL punctuation is not mistaken for a marker, and real markers survive.
    parsed = Requirement(result.source.split(" #", 1)[0].strip())
    assert str(parsed.specifier) == "<3,>=2"
    assert parsed.marker == Requirement(source.split(" #", 1)[0].strip()).marker
    assert not result.manual


def test_preserved_when_generated_requirement_is_invalid() -> None:
    # Given mapping data which cannot produce a valid requirement.
    source = "daytona==0.224.0\n"
    mapping = replace(load_mapping(), specifier="not a version")
    # When rendering the replacement.
    result = inspect_manifest(Path("requirements.txt"), source, mapping)
    # Then the original remains intact with a manual dependency item.
    assert result.source == source
    assert result.changes == ()
    assert result.manual
