# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared YAML scalar cases exercise the real migration CLI in both ecosystems."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, TypedDict

import pytest

from daytona_upgrade.cli import main


class YamlOptions(TypedDict, total=False):
    suffix: str
    expected: str


class YamlCase(YamlOptions):
    name: str
    source: str


CASES: Final[list[YamlCase]] = json.loads(
    (Path(__file__).parents[2] / "mapping" / "yaml-environment-cases.json").read_text()
)
ENDPOINTS: Final = ("https://self.hosted", "https://app.daytona.io/api")


@pytest.mark.parametrize(
    "case",
    [
        (
            row["source"].replace("ENDPOINT", endpoint + row.get("suffix", "")),
            row.get("expected", "")
            or (
                "cloud"
                if endpoint == ENDPOINTS[1] and not row.get("suffix", "")
                else "custom"
            ),
        )
        for row in CASES
        for endpoint in ENDPOINTS
    ],
    ids=[f"{row['name']}-{endpoint}" for row in CASES for endpoint in ENDPOINTS],
)
def test_migrates_only_cloud_projects_when_yaml_has_scalar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], case: tuple[str, str]
) -> None:
    # Given
    yaml, outcome = case
    custom = outcome == "custom"
    reported = outcome != "none"
    originals = {
        "requirements.txt": "daytona==0.224.0\n",
        "app.py": "from daytona import Daytona\nDaytona().create()\n",
        "compose.yaml": yaml,
    }
    for name, source in originals.items():
        (tmp_path / name).write_text(source)
    # When
    code = main([str(tmp_path), "--json", "--yes"])
    # Then
    report = json.loads(capsys.readouterr().out)
    assert code == (2 if custom else 0)
    assert [item["kind"] for item in report["items"]] == (
        []
        if not reported
        else (["env-api-url", "custom-api-url"] if custom else ["env-api-url"])
    )
    assert (tmp_path / "compose.yaml").read_text() == yaml
    if not custom:
        assert "Daytona().v0.create()" in (tmp_path / "app.py").read_text()
    else:
        assert {name: (tmp_path / name).read_text() for name in originals} == originals
