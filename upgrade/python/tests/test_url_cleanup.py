# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Default URL writes disappear without leaving redundant statements."""

from __future__ import annotations

import ast

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize("value", ["None", '"https://app.daytona.io/api"'])
@pytest.mark.parametrize(
    "write",
    [
        "cfg.api_url = VALUE",
        "cfg.server_url: str = VALUE",
        'setattr(cfg, "api_url", VALUE)',
    ],
)
@pytest.mark.parametrize(
    "layout",
    [
        ("WRITE\n", ""),
        ("WRITE; keep()\n", "keep()\n"),
        ("keep(); WRITE\n", "keep()\n"),
        ("first(); WRITE; last()\n", "first(); last()\n"),
        ("WRITE; WRITE\n", ""),
        ("if enabled: WRITE\n", "if enabled: pass\n"),
        ("if enabled: WRITE; keep()\n", "if enabled: keep()\n"),
        ("if enabled: keep(); WRITE\n", "if enabled: keep()\n"),
        ("if enabled: WRITE; WRITE\n", "if enabled: pass\n"),
        ("if enabled:\n    WRITE\n", "if enabled:\n    pass\n"),
        (
            "if enabled:\n    WRITE\n    keep()\n",
            "if enabled:\n    keep()\n",
        ),
        (
            "def run():\n    if enabled:\n        WRITE\n    keep()\n",
            "def run():\n    if enabled:\n        pass\n    keep()\n",
        ),
        (
            "try:\n    WRITE\nexcept Error:\n    WRITE\nfinally:\n    keep()\n",
            "try:\n    pass\nexcept Error:\n    pass\nfinally:\n    keep()\n",
        ),
    ],
)
def test_statement_disappears_when_default_url_write_is_standalone(
    value: str, write: str, layout: tuple[str, str]
) -> None:
    # Given a proven config and a URL write among real sibling statements.
    prefix = "import daytona\ncfg = daytona.DaytonaConfig()\n"
    before, after = layout
    source = prefix + before.replace("WRITE", write.replace("VALUE", value))
    # When upgrading the obsolete default-setting statement.
    result = migrate(source)
    # Then only emptied suites need pass; other statements keep their order.
    assert result.items == ()
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(prefix + after))


@pytest.mark.parametrize(
    "expression,expected",
    [
        ('result = setattr(cfg, "api_url", None)', "result = None"),
        ('consume(setattr(cfg, "api_url", None))', "consume(None)"),
        ('[setattr(cfg, "api_url", None)]', "[None]"),
        ("other = cfg.api_url = None", "other = None"),
        ("None", "None"),
        ("pass", "pass"),
    ],
)
def test_expression_survives_when_default_url_write_has_a_value_consumer(
    expression: str, expected: str
) -> None:
    # Given a nested call, chained assignment, or unrelated existing no-op.
    prefix = "import daytona\ncfg = daytona.DaytonaConfig()\n"
    # When migrating while preserving the builtin's None return value.
    result = migrate(prefix + expression + "\n")
    # Then cleanup removes only obsolete whole statements.
    assert result.items == ()
    assert ast.dump(ast.parse(result.source)) == ast.dump(
        ast.parse(prefix + expected + "\n")
    )
