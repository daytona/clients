# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unknown receivers require linked evidence or a Daytona-specific identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade import cli


@pytest.mark.parametrize("receiver", ["client", "http_client", "owner.client"])
@pytest.mark.parametrize("member", ["create", "get", "list", "delete", "snapshot"])
@pytest.mark.parametrize(
    "access",
    [
        "{receiver}.{member}",
        "getattr({receiver}, '{member}')",
        "hasattr({receiver}, '{member}')",
    ],
)
def test_unknown_generic_receiver_stays_when_sdk_use_is_unrelated(
    receiver: str, member: str, access: str
) -> None:
    # Given real Daytona usage elsewhere, but no flow linking it to this parameter.
    expression = access.format(receiver=receiver, member=member)
    source = (
        "from daytona import Daytona\nsdk = Daytona()\nsdk.create()\n"
        f"def run(client, http_client, owner):\n    return {expression}\n"
    )
    # When upgrading the module.
    result = migrate(source)
    # Then only proven SDK usage changes; the unrelated receiver gets no manual item.
    assert result.source == source.replace("sdk.create()", "sdk.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "receiver", ["daytona", "daytona_client", "owner.daytona", "DAYTONA"]
)
@pytest.mark.parametrize(
    "access",
    [
        "{receiver}.create",
        "getattr({receiver}, 'create')",
        "hasattr({receiver}, 'create')",
    ],
)
def test_unknown_receiver_requires_review_when_identity_names_daytona(
    receiver: str, access: str
) -> None:
    # Given explicit Daytona identity, but no constructor or annotation proof.
    expression = access.format(receiver=receiver)
    source = (
        "from daytona import Daytona\n"
        f"def run(daytona, daytona_client, owner, DAYTONA):\n    return {expression}\n"
    )
    # When upgrading the unresolved receiver.
    result = migrate(source)
    # Then identity justifies review, never an executable rewrite.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert f"return {expression}\n" in result.source


@pytest.mark.parametrize("receiver", ["client", "http_client", "value"])
@pytest.mark.parametrize(
    "access",
    [
        "{receiver}.create",
        "getattr({receiver}, 'create')",
        "hasattr({receiver}, 'create')",
    ],
)
def test_generic_receiver_requires_review_when_value_flow_links_daytona(
    receiver: str, access: str
) -> None:
    # Given a decorated factory whose return annotation links the uncertain value.
    expression = access.format(receiver=receiver)
    source = (
        "from daytona import Daytona\n"
        "@unknown_decorator\ndef factory() -> Daytona:\n    return Daytona()\n"
        f"{receiver} = factory()\nresult = {expression}\n"
    )
    # When proof is insufficient for an automatic rewrite.
    result = migrate(source)
    # Then linked provenance still produces a manual item, regardless of spelling.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert f"result = {expression}\n" in result.source


def test_strict_cli_stays_clean_when_generic_parameter_has_no_linked_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a project with a genuine SDK call plus an independent HTTP-like parameter.
    source = (
        "from daytona import Daytona\nsdk = Daytona()\nsdk.create()\n"
        "def fetch(http_client):\n    return http_client.get('/health')\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona>=0.224,<1\n")
    # When running strict JSON mode through the public CLI.
    status = cli.main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    report = json.loads(capsys.readouterr().out)
    # Then unrelated receiver spelling cannot make strict migration fail.
    assert status == 0
    assert report["items"] == []
    assert (tmp_path / "app.py").read_text() == source.replace(
        "sdk.create()", "sdk.v0.create()"
    )
