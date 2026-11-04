# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mock object targets follow the same receiver proofs as migrated calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    mapping = load_mapping()
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )


@pytest.mark.parametrize(
    "api",
    [
        ("from unittest.mock import patch", "patch.object"),
        ("from unittest.mock import patch as p", "p.object"),
        ("from unittest import mock", "mock.patch.object"),
        ("from unittest import mock as m", "m.patch.object"),
        ("import unittest.mock", "unittest.mock.patch.object"),
        ("import unittest.mock as m", "m.patch.object"),
        ("import unittest as u", "u.mock.patch.object"),
    ],
)
@pytest.mark.parametrize(
    "form",
    [
        "@{call}\ndef test_work(mock):\n    d.create()\n",
        "with {call}:\n    d.create()\n",
        "mock = {call}.start()\nd.create()\n",
    ],
)
@pytest.mark.parametrize("client_type", ["Daytona", "AsyncDaytona"])
def test_patch_routes_when_receiver_is_proven(
    api: tuple[str, str], form: str, client_type: str
) -> None:
    # Given every imported patch spelling and invocation form on a proven client.
    imports, callee = api
    prefix = f"{imports}\nfrom daytona import {client_type}\nd = {client_type}()\n"
    source = prefix + form.format(call=f'{callee}(d, "create", return_value=sentinel)')
    # When migrating the mock target and its workload together.
    result = migrate(source)
    # Then both target the same compatibility client, without manual items.
    assert result.source == source.replace('(d, "create"', '(d.v0, "create"').replace(
        "d.create()", "d.v0.create()"
    )
    assert result.items == ()


@pytest.mark.parametrize("member", sorted(load_mapping().moved))
def test_patch_routes_when_member_is_moved(member: str) -> None:
    # Given the same moved-member inventory used by attribute rewriting.
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona\nd = Daytona()\n"
        f"patch.object(d, {member!r})\n"
    )
    # When migrating a literal member target.
    result = migrate(source)
    # Then the target gains exactly one compatibility namespace.
    assert result.source == source.replace("patch.object(d,", "patch.object(d.v0,")
    assert result.items == ()


@pytest.mark.parametrize(
    "case",
    [
        ("d = Daytona()\nalias = d", "alias"),
        ("def run(d: Daytona):", "d"),
        ("class Owner:\n    client: Daytona\n    def run(self):", "self.client"),
        ("def factory() -> Daytona:\n    return Daytona()", "factory()"),
        ("class Client(Daytona):\n    pass\nd = Client()", "d"),
        ("", "Daytona()"),
    ],
)
def test_patch_routes_when_call_proof_supports_receiver(case: tuple[str, str]) -> None:
    # Given aliases, annotations, attributes, factories and inherited SDK members.
    setup, receiver = case
    indent = (
        "        "
        if receiver == "self.client"
        else "    "
        if setup.endswith(":")
        else ""
    )
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona\n"
        f"{setup}\n{indent}patch.object({receiver}, 'create')\n"
    )
    # When applying the existing call proof to the mock receiver.
    result = migrate(source)
    # Then the proven target routes without changing the member string.
    assert result.source == source.replace(
        f"patch.object({receiver},", f"patch.object({receiver}.v0,"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "call",
    [
        'patch.object(target=d, attribute="create", new=fake)',
        'patch.object(attribute="create", new=fake, target=d)',
        'patch.object(d, attribute="create", new=fake)',
        'patch.object(d, "cre" "ate", new=fake)',
    ],
)
def test_patch_routes_when_arguments_use_supported_literal_forms(call: str) -> None:
    # Given keyword arguments and compile-time concatenated string literals.
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona\nd = Daytona()\n"
        + call
        + "\n"
    )
    # When migrating without disturbing argument order or spelling.
    result = migrate(source)
    # Then only the receiver expression changes.
    assert result.source == source.replace("target=d", "target=d.v0").replace(
        "patch.object(d,", "patch.object(d.v0,"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "operation",
    [
        'patch.object(Daytona, "create")',
        'patch.object(AsyncDaytona, "get")',
        "patch.object(d, member)",
        'patch.object(d, "close")',
        "patch.object(d, *members)",
        "patch.object(d, **options)",
        'patch("daytona.Daytona.create")',
    ],
)
def test_patch_reports_when_target_cannot_route(operation: str) -> None:
    # Given class targets, unknown receivers, dynamic members or unmoved members.
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona, AsyncDaytona\nd = Daytona()\n"
        + operation
        + "\n"
    )
    # When inspecting the unresolved mock target.
    result = migrate(source)
    # Then the existing diagnostic remains and the target is not rewritten.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_patch_reports_when_subclass_owns_member() -> None:
    # Given a project override rather than an inherited SDK member.
    source = "from unittest.mock import patch\nfrom daytona import Daytona\nclass Client(Daytona):\n    def create(self):\n        pass\nd = Client()\npatch.object(d, 'create')\n"
    # When applying member ownership rules.
    result = migrate(source)
    # Then the project method remains the target of the patch.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("target", ["unknown", "object()"])
def test_patch_stays_unchanged_without_daytona_link(target: str) -> None:
    # Given an unbound name or unrelated object, even in a Daytona-using file.
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\nd = Daytona()\npatch.object({target}, 'create')\n"
    # When inspecting a target without linked Daytona provenance.
    result = migrate(source)
    # Then no rewrite or manual item is warranted.
    assert result.source == source
    assert result.items == ()


def test_patch_is_idempotent_when_target_is_already_migrated() -> None:
    # Given an already migrated patch and call.
    source = "from unittest.mock import patch\nfrom daytona import Daytona\nd = Daytona()\nwith patch.object(d.v0, 'create'):\n    d.v0.create()\n"
    # When rerunning the migration.
    result = migrate(source)
    # Then no second namespace or manual item appears.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("receiver", ["d", "Daytona"])
def test_cli_strict_status_when_patch_target_requires_proof(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], receiver: str
) -> None:
    # Given a real project with a patch target and a root-client call.
    app = tmp_path / "app.py"
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\nd = Daytona()\nwith patch.object({receiver}, 'create'):\n    d.create()\n"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    # When the CLI migrates the project through its strict JSON interface.
    status = main([str(tmp_path), "--json", "--strict"])
    # Then only a class target still needs manual intervention.
    report = json.loads(capsys.readouterr().out)
    assert status == (0 if receiver == "d" else 1)
    assert [item["kind"] for item in report["items"]] == (
        [] if receiver == "d" else ["mock-target"]
    )
    expected = source.replace("d.create()", "d.v0.create()")
    if receiver == "d":
        expected = expected.replace("patch.object(d,", "patch.object(d.v0,")
    assert app.read_text() == expected
