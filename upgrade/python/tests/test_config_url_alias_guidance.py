# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL guidance follows current config bindings rather than stale aliases."""

from __future__ import annotations

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade.config_messages import SHARED_URL_WRITE, UNRESOLVED_URL_FIELDS


@pytest.mark.parametrize(
    "body",
    [
        "cfg = DaytonaConfig()\nalias = cfg\ncfg = DaytonaConfig()\nalias.api_url = url\nDaytona(cfg)\n",
        "cfg = DaytonaConfig()\nalias = cfg\nalias = DaytonaConfig()\nalias.api_url = url\nDaytona(cfg)\n",
        "cfg = DaytonaConfig(api_url=url)\ncfg = DaytonaConfig()\ncustomize(cfg)\nDaytona(cfg)\n",
        "cfg = DaytonaConfig()\nalias = cfg\nfrom helper import cfg\nalias.api_url = url\nDaytona(cfg)\n",
    ],
)
@pytest.mark.parametrize("scope", ["module", "function"])
def test_uses_url_free_guidance_when_url_write_targets_an_old_binding(
    body: str, scope: str
) -> None:
    # Given a visible URL write disconnected from the config at the client call.
    source = "from daytona import Daytona, DaytonaConfig\n"
    source += (
        body
        if scope == "module"
        else "def run():\n"
        + "".join("    " + line + "\n" for line in body.splitlines())
    )
    # When an unresolved constructor is retained under the Cloud assertion.
    result = migrate(source, drop=True)
    # Then its guidance does not claim that an unrelated URL write must be kept.
    client_line = next(
        index
        for index, line in enumerate(result.source.splitlines())
        if "Daytona(cfg)" in line
    )
    assert result.source.splitlines()[client_line - 1].strip() == (
        "# FIXME(daytona-upgrade): " + UNRESOLVED_URL_FIELDS
    )


@pytest.mark.parametrize(
    "body",
    [
        "cfg = DaytonaConfig()\nalias = cfg\nalias.api_url = url\nDaytona(cfg)\n",
        "cfg = DaytonaConfig()\nalias = cfg\ncfg = DaytonaConfig()\nalias.api_url = url\nDaytona(alias)\n",
        "cfg = DaytonaConfig()\nDaytona(cfg)\nalias = cfg\nalias.api_url = url\n",
    ],
)
def test_keeps_url_write_guidance_when_alias_still_targets_the_client_config(
    body: str,
) -> None:
    # Given a current alias, including writes after construction.
    source = "from daytona import Daytona, DaytonaConfig\n" + body
    # When the unresolved call is retained.
    result = migrate(source, drop=True)
    # Then the client guidance still protects the write to that same object.
    assert "# FIXME(daytona-upgrade): " + SHARED_URL_WRITE in result.source
