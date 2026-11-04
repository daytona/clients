# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unknown keyword expansions retain the config refusal in either order."""

from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "case",
    [
        (field, first, inline, drop)
        for field in ("api_url", "server_url")
        for first in (False, True)
        for inline in (False, True)
        for drop in (False, True)
    ],
)
def test_config_stays_unresolved_when_keywords_include_unknown_expansion(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, bool, bool, bool],
) -> None:
    # Given
    field, first, inline, drop = case
    explicit = f'{field}="https://app.daytona.io/api"'
    args = f"{explicit}, **custom" if first else f"**custom, {explicit}"
    config = f"DaytonaConfig({args})"
    call = (
        f"Daytona({config}).create()"
        if inline
        else f"config = {config}\nDaytona(config).create()"
    )
    source = f"from daytona import Daytona, DaytonaConfig\n{call}\n"
    app = tmp_path / "app.py"
    requirements = tmp_path / "requirements.txt"
    app.write_text(source)
    requirements.write_text("daytona==0.224.0\n")
    # When
    status = main(
        [str(tmp_path), "--yes", "--json", *(["--drop-api-url"] if drop else [])]
    )
    # Then
    output = capsys.readouterr().out
    assert status == (0 if drop else 2)
    assert f'"kind": "{"config-field" if drop else "custom-api-url"}"' in output
    if drop:
        assert config in app.read_text()
    else:
        assert '"reason": "custom-api-url"' in output
        assert app.read_text() == source
        assert requirements.read_text() == "daytona==0.224.0\n"
