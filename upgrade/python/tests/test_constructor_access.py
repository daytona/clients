# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Credential diagnostics distinguish construction from protected first use."""

from __future__ import annotations

from pathlib import Path
from textwrap import indent

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "body",
    [
        "return Daytona().list()",
        "return Daytona().v0.list()",
        "c = Daytona()\nc.list()",
        "c = Daytona()\nv0 = c.v0",
        "c: Daytona = Daytona()\nc.v0",
        "self.c = Daytona()\nself.c.v0",
        "(c := Daytona()).v0",
        "c = Daytona()\nalias = c\nalias.v0",
        "c = Daytona()\ngetattr(c, 'list')()",
        "c = Daytona()\nhasattr(c, 'list')",
    ],
)
def test_strict_succeeds_when_try_accesses_constructed_client(
    tmp_path: Path, body: str
) -> None:
    # Given a first v0 access protected by the same legacy handler.
    source = (
        "from daytona import Daytona, DaytonaError\ndef optional():\n    try:\n"
        + indent(body, "        ")
        + "\n    except DaytonaError:\n        return None\n"
    )
    path = tmp_path / "app.py"
    path.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the actual CLI rewrites imports and calls in strict mode.
    code = main([str(tmp_path), "--yes", "--strict"])
    # Then valid credential protection needs no manual intervention.
    assert code == 0
    assert "FIXME(daytona-upgrade)" not in path.read_text()


@pytest.mark.parametrize(
    "body, suffix",
    [
        ("c = Daytona()", "\nc.v0\n"),
        ("c = Daytona()\ndef later():\n    return c.v0", ""),
        ("c = Daytona()\nlater = lambda: c.v0", ""),
        ("c = Daytona()\nlater = (c.v0 for _ in values)", ""),
        ("c = Daytona()\nother = Daytona()\nother.v0", ""),
        ("c = Daytona()\nc = Daytona()\nc.v0", ""),
        ("c = Daytona()\nc = other\nc.v0", ""),
    ],
)
def test_warns_when_v0_access_does_not_protect_this_construction(
    body: str, suffix: str
) -> None:
    # Given absent, deferred, unrelated or rebound access to a client namespace.
    source = (
        "from daytona import Daytona, DaytonaError\ntry:\n"
        + indent(body, "    ")
        + "\nexcept DaytonaError:\n    pass\n"
        + suffix
    )
    # When upgrading the legacy catch boundary.
    result = migrate(source)
    # Then the original construction still needs a first-use review.
    assert any(item["kind"] == "fixme" and item["line"] == 3 for item in result.items)


def test_uses_walrus_receiver_when_constructor_is_an_argument() -> None:
    # Given a named expression, not a direct statement assignment.
    source = "from daytona import Daytona\ntry:\n    register(c := Daytona())\nexcept Exception:\n    pass\n"
    # When generating constructor guidance.
    result = migrate(source)
    # Then the diagnostic identifies the actual client binding.
    assert len(result.items) == 1
    assert "c.v0" in result.items[0]["message"]
