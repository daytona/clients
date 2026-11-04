# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""URL write safety, including the CLI's all-or-nothing refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str, *, drop: bool = False) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping, drop_api_url=drop),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize("value", ['"https://self.example/api"', "url", "get_url()"])
@pytest.mark.parametrize(
    "write",
    [
        "cfg.api_url = VALUE",
        "cfg.server_url: str = VALUE",
        'setattr(cfg, "api_url", VALUE)',
        'setattr(cfg, "server_url", VALUE)',
        "cfg.api_url += VALUE",
    ],
)
def test_cli_refuses_all_writes_when_url_assignment_is_custom(
    tmp_path: Path, value: str, write: str
) -> None:
    # Given a proven configuration binding and an old dependency pin.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        "cfg = DaytonaConfig()\n"
        + write.replace("VALUE", value)
        + "\nDaytona().create()\n"
    )
    app = tmp_path / "app.py"
    requirement = tmp_path / "requirements.txt"
    app.write_text(source)
    requirement.write_text("daytona==0.224.0\n")
    # When preflighting the whole project through its CLI.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then no partial source or dependency update escapes the refusal.
    assert status == 2
    assert app.read_text() == source
    assert requirement.read_text() == "daytona==0.224.0\n"


@pytest.mark.parametrize("value", ['"https://app.daytona.io/api"', "None"])
@pytest.mark.parametrize(
    "write",
    [
        "cfg.api_url = VALUE",
        "cfg.server_url: str = VALUE",
        'setattr(cfg, "api_url", VALUE)',
    ],
)
def test_url_write_is_removed_when_value_means_cloud(value: str, write: str) -> None:
    # Given a default URL write on a proven configuration binding.
    source = (
        "import daytona\ncfg = daytona.DaytonaConfig()\n"
        + write.replace("VALUE", value)
        + "\n"
    )
    # When migrating the obsolete field.
    result = migrate(source)
    # Then no obsolete assignment remains and no refusal is needed.
    assert "api_url" not in result.source
    assert "server_url" not in result.source
    assert result.items == ()


@pytest.mark.parametrize(
    "write",
    [
        "cfg.api_url = url",
        'setattr(cfg, "api_url", url)',
    ],
)
def test_url_write_stays_manual_when_cloud_usage_is_asserted(write: str) -> None:
    # Given a proven configuration write whose value cannot safely be discarded.
    source = "import daytona\ncfg: daytona.DaytonaConfig\n" + write + "\n"
    # When the operator explicitly takes responsibility for the URL.
    result = migrate(source, drop=True)
    # Then the write stays user-owned and explicitly reported.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["config-field"]


def test_none_keyword_is_dropped_when_default_url_is_requested() -> None:
    # Given 0.x's explicit spelling of its default URL.
    source = "from daytona import DaytonaConfig\ncfg = DaytonaConfig(api_url=None)\n"
    # When migrating the constructor.
    result = migrate(source)
    # Then the default is retained without a custom-URL refusal.
    assert result.source == source.replace("api_url=None", "")
    assert result.items == ()


def test_url_assignment_stays_when_file_has_no_daytona_import() -> None:
    # Given an unrelated library using the same field name.
    source = 'cfg.api_url = "https://app.daytona.io/api"\n'
    # When inspecting the file.
    result = migrate(source)
    # Then no Daytona-specific policy applies.
    assert result.source == source
    assert result.items == ()


def test_nested_target_is_refused_when_value_cannot_be_mapped_to_url() -> None:
    # Given an unpacking store that may conceal a custom URL.
    source = "import daytona\ncfg: daytona.DaytonaConfig\ncfg.api_url, key = settings\n"
    # When preflighting URL mutations.
    result = migrate(source)
    # Then the tool refuses instead of silently missing the write.
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


def test_other_target_is_retained_when_chained_cloud_write_is_dropped() -> None:
    # Given an assignment with an independent side target.
    source = (
        "import daytona\ncfg = daytona.DaytonaConfig()\n"
        'url = cfg.api_url = "https://app.daytona.io/api"\n'
    )
    # When removing the obsolete configuration field.
    result = migrate(source)
    # Then the independent assignment still runs.
    assert result.source == source.replace("cfg.api_url = ", "")


def test_nested_client_call_moves_when_setattr_is_not_a_url_write() -> None:
    # Given an unrelated setattr call containing a proven SDK invocation.
    source = 'from daytona import Daytona\nsetattr(obj, "value", Daytona().create())\n'
    # When checking setattr for obsolete URL keys.
    result = migrate(source)
    # Then the check does not discard child rewrites.
    assert result.source == source.replace(
        "Daytona().create()", "Daytona().v0.create()"
    )
