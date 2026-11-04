# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL mutation rewrites must retain user call semantics and evaluation."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "shadow",
    [
        'def setattr(obj, key, value):\n    record(obj, key, value)\ncfg: daytona.DaytonaConfig\nsetattr(cfg, "api_url", None)',
        'def run(setattr, cfg: daytona.DaytonaConfig):\n    setattr(cfg, "api_url", None)',
        'def run(cfg: daytona.DaytonaConfig):\n    setattr(cfg, "api_url", None)\n    setattr = custom',
        'builtins = custom\ncfg: daytona.DaytonaConfig\nbuiltins.setattr(cfg, "api_url", None)',
    ],
)
def test_custom_setattr_stays_when_builtin_identity_is_unproven(shadow: str) -> None:
    # Given a proven config and a callable, parameter, local or qualified shadow.
    source = "import daytona\n" + shadow + "\n"
    # When inspecting a URL-key call without builtin identity.
    result = migrate(source)
    # Then the callable's behavior is not replaced by a literal None.
    assert result.source == source
    assert any(item["kind"] == "custom-api-url" for item in result.items)


@pytest.mark.parametrize(
    "write",
    [
        'setattr(build_config(), "api_url", None)',
        "build_config().api_url = None",
        "build_config().server_url: str = None",
        "other = build_config().api_url = None",
        "holder.config.api_url = None",
        'setattr(configs[index()], "api_url", None)',
    ],
)
@pytest.mark.parametrize("drop", [False, True])
def test_url_write_stays_untouched_when_receiver_identity_is_unproven(
    write: str, drop: bool
) -> None:
    # Given an unproven non-name receiver with observable evaluation.
    source = "import daytona\n" + write + "\n"
    # When upgrading with or without the explicit Cloud assertion.
    result = migrate(source, drop=drop)
    # Then unrelated receiver evaluation survives without a Daytona diagnostic.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "imports,callee",
    [
        ("", "setattr"),
        ("import builtins\n", "builtins.setattr"),
        ("import builtins as b\n", "b.setattr"),
        ("from builtins import setattr as set_option\n", "set_option"),
    ],
)
def test_default_write_drops_when_builtin_and_simple_receiver_are_proven(
    imports: str, callee: str
) -> None:
    # Given an unshadowed builtin operating on a simple config binding.
    prefix = "import daytona\n" + imports + "cfg = daytona.DaytonaConfig()\n"
    source = prefix + f'{callee}(cfg, "api_url", None)\n'
    # When removing the obsolete default-setting call.
    result = migrate(source)
    # Then the safe builtin call still migrates with no manual item.
    assert result.source == prefix
    assert result.items == ()


@pytest.mark.parametrize("drop", [False, True])
def test_cli_migrates_dependencies_when_receiver_identity_is_unproven(
    tmp_path: Path, drop: bool
) -> None:
    # Given an unproven side-effectful receiver and an old dependency pin.
    source = 'import daytona\nsetattr(build_config(), "api_url", None)\n'
    app = tmp_path / "app.py"
    deps = tmp_path / "requirements.txt"
    app.write_text(source)
    deps.write_text("daytona==0.224.0\n")
    # When running the actual CLI, optionally accepting responsibility for Cloud.
    status = cli.main(
        [str(tmp_path), "--force", "--yes", *(["--drop-api-url"] if drop else [])]
    )
    # Then the unrelated receiver stays untouched while dependencies migrate.
    assert status == 0
    assert app.read_text() == source
    assert deps.read_text() == "daytona[v0-sdk]>=2,<3\n"
