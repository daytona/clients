# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""CLI regressions for bounded external-call provenance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "program",
    [
        f"{setup}\n{call}"
        for setup, receiver in [
            (
                "from mycorp_infra.clients import make_daytona\ndaytona = make_daytona()",
                "daytona",
            ),
            ("from mycorp import DaytonaFactory\nc = DaytonaFactory()", "c"),
            ("from mycorp import DaytonaFactory as Factory\nc = Factory()", "c"),
            ("import mycorp.daytona_clients as infra\nc = infra.Factory()", "c"),
            ("import redis\nDaYtOnA = redis.Redis()", "DaYtOnA"),
            ("import httpx\ndaytona_client = httpx.Client()", "daytona_client"),
            ('import boto3\ndaytona = boto3.client("s3")', "daytona"),
            ("from mycorp import make_client\nclient = make_client()", "client"),
            ("import mycorp\nclient = mycorp.make_client()", "client"),
            ("from mycorp import make_daytona\nc = make_daytona()", "c"),
        ]
        for call in (f"{receiver}.create()", f'getattr({receiver}, "create")()')
    ],
)
def test_strict_reviews_external_result_only_when_daytona_identity_is_linked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], program: str
) -> None:
    # Given an unresolved external wrapper or a Daytona-named external result.
    source = f"from daytona import Daytona\n{program}\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.224.0\n")
    # When running the upgrade through the CLI with strict writes enabled.
    status = main([str(tmp_path), "--force", "--strict", "--json"])
    # Then linked Daytona identity merits review, not a generic factory name.
    report = json.loads(capsys.readouterr().out)
    daytona_identity = "daytona" in program.lower()
    assert status == (1 if daytona_identity else 0)
    assert [item["kind"] for item in report["items"]] == (
        ["fixme"] if daytona_identity else []
    )
    assert program.splitlines()[-1] in app.read_text()
    assert ("# FIXME(daytona-upgrade):" in app.read_text()) == daytona_identity


@pytest.mark.parametrize(
    "setup",
    [
        "import redis\nclient = redis.Redis()",
        "from redis import Redis as Cache\nclient = Cache()",
        "import httpx\nclient = httpx.Client()",
        "from httpx import Client\nclient = Client()",
        'import boto3\nclient = boto3.client("s3")',
        'from boto3 import client as make\nclient = make("s3")',
    ],
)
def test_strict_succeeds_when_external_constructor_or_known_factory_is_unrelated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], setup: str
) -> None:
    # Given concrete unrelated constructor/factory evidence, not merely an import.
    source = f"from daytona import Daytona\n{setup}\nclient.create()\n"
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona-sdk==0.224.0\n")
    # When upgrading under strict mode.
    status = main([str(tmp_path), "--force", "--strict", "--json"])
    # Then no manual work or source edits are invented for unrelated clients.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert app.read_text() == source
