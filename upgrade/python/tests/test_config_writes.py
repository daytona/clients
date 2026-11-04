# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL mutations preserve values owned by callers or other config readers."""

from __future__ import annotations

import ast

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize(
    "declaration",
    [
        "def use(config: DaytonaConfig):\n{body}",
        "from settings import config\n{body}\nprint(config.api_url)\n",
        "config: DaytonaConfig = obtain_config()\n{body}",
        "config = DaytonaConfig()\n{body}\nprint(config.api_url)\n",
    ],
)
@pytest.mark.parametrize(
    "write",
    [
        'config.api_url = "https://app.daytona.io/api"',
        'setattr(config, "api_url", "https://app.daytona.io/api")',
        'config.server_url = "https://app.daytona.io/api"',
    ],
)
def test_retains_write_when_config_ownership_is_shared_or_unresolved(
    declaration: str, write: str
) -> None:
    # Given a caller-owned or shared config with a proven Cloud URL write.
    body = write + "\nclient = Daytona(config)\n"
    if declaration.startswith("def"):
        body = "\n".join("    " + line for line in body.splitlines()) + "\n"
    source = "from daytona import Daytona, DaytonaConfig\n" + declaration.format(
        body=body
    )

    # When an explicit Cloud assertion allows unresolved constructor inputs.
    result = migrate(source, drop=True)

    # Then URL data survives; the user must give the client a URL-free config.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "config-field" for item in result.items)
    assert "FIXME(daytona-upgrade)" in result.source
