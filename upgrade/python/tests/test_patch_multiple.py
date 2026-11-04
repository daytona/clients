# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Multi-member mock targets share the object-patch receiver proof."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import SourceResult, TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def migrate(source: str) -> SourceResult:
    mapping = load_mapping()
    sources = {
        "app": "from daytona import Daytona\nclient = Daytona()\n",
        "test_app": source,
    }
    return transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", build_index(sources, mapping)),
    )


@pytest.mark.parametrize(
    "api",
    [
        ("from unittest.mock import patch", "patch.multiple"),
        ("from unittest.mock import patch as p", "p.multiple"),
        ("from unittest import mock", "mock.patch.multiple"),
        ("import unittest.mock", "unittest.mock.patch.multiple"),
        ("import unittest.mock as m", "m.patch.multiple"),
        ("", "mocker.patch.multiple"),
    ],
)
@pytest.mark.parametrize(
    "form",
    [
        "{call}\n",
        "@{call}\ndef test_work(**mocks):\n    app.client.create()\n",
        "with {call}:\n    app.client.create()\n",
        "mocks = {call}.start()\napp.client.create()\n",
    ],
)
def test_multiple_routes_when_all_members_are_moved(
    api: tuple[str, str], form: str
) -> None:
    # Given a project-owned client and every supported patch spelling/form.
    imports, callee = api
    source = f"{imports}\nimport app\n" + form.format(
        call=f"{callee}(app.client, create=mock.DEFAULT, get=fake)"
    )
    # When migrating targets alongside their workload.
    result = migrate(source)
    # Then the patch and workload select the same compatibility client.
    assert result.source == source.replace("(app.client,", "(app.client.v0,").replace(
        "app.client.create()", "app.client.v0.create()"
    )
    assert result.items == ()


@pytest.mark.parametrize("member", sorted(set(load_mapping().moved) - {"create"}))
def test_multiple_routes_when_keyword_is_any_moved_member(member: str) -> None:
    # Given the complete root-member inventory, with a keyword target.
    source = f"from unittest.mock import patch\nimport app\npatch.multiple({member}=fake, target=app.client)\n"
    # When migrating the named target without reordering arguments.
    result = migrate(source)
    # Then only its receiver changes.
    assert result.source == source.replace("target=app.client", "target=app.client.v0")
    assert result.items == ()


@pytest.mark.parametrize(
    "setup,receiver",
    [
        ("client = Daytona()", "client"),
        ("client = Daytona()\nalias = client", "alias"),
        ("def run(client: Daytona):", "client"),
        ("class Client(Daytona):\n    pass\nclient = Client()", "client"),
    ],
)
def test_multiple_routes_when_object_patch_proof_supports_receiver(
    setup: str, receiver: str
) -> None:
    # Given the existing constructor, alias, annotation and subclass proofs.
    indent = "    " if setup.endswith(":") else ""
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\n{setup}\n{indent}patch.multiple({receiver}, get=fake)\n"
    # When applying the member ownership proof to a multi-member patch.
    result = migrate(source)
    # Then the target receives exactly one namespace.
    assert result.source == source.replace(
        f"patch.multiple({receiver},", f"patch.multiple({receiver}.v0,"
    )
    assert result.items == ()


@pytest.mark.parametrize(
    "keywords",
    ["get=fake, close=other", "get=fake, **options"],
)
def test_multiple_requires_split_when_members_cannot_all_move(keywords: str) -> None:
    # Given a proven client but an unsafe mixture of patched members/options.
    source = f"from unittest.mock import patch\nimport app\npatch.multiple(app.client, {keywords})\n"
    # When inspecting the indivisible target.
    result = migrate(source)
    # Then it remains unchanged with an actionable manual-review comment.
    assert source.splitlines()[-1] in result.source
    assert "app.client.v0" not in result.source
    assert "# FIXME(daytona-upgrade):" in result.source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "target",
    ["Daytona", "client"],
)
def test_multiple_reports_when_receiver_cannot_be_proven(target: str) -> None:
    # Given a class, an opaque value, or a subclass overriding the moved member.
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\nclass Client(Daytona):\n    def create(self):\n        pass\nclient = Client()\npatch.multiple({target}, create=fake)\n"
    # When resolving SDK member ownership.
    result = migrate(source)
    # Then no guessed receiver rewrite occurs.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_multiple_is_idempotent_when_receiver_already_uses_v0() -> None:
    # Given an already migrated client target.
    source = "from unittest.mock import patch\nimport app\npatch.multiple(app.client.v0, create=fake, get=other)\n"
    # When rerunning migration.
    result = migrate(source)
    # Then no duplicate namespace or review item appears.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("target", ["unknown", "object()"])
def test_multiple_stays_unchanged_without_daytona_link(target: str) -> None:
    # Given an unbound name or unrelated object in a Daytona-using file.
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\npatch.multiple({target}, create=fake)\n"
    # When inspecting a target without linked Daytona provenance.
    result = migrate(source)
    # Then the target stays untouched without manual review.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "setup",
    [
        "client = Daytona()\nclient = object()",
        "client = Daytona() if condition else object()",
        "class Client(Daytona):\n    def get(self):\n        pass\nclient = Client()",
    ],
)
def test_multiple_reports_when_any_member_lacks_sdk_ownership(setup: str) -> None:
    # Given lost client proofs or a subclass replacing only one patched member.
    source = f"from unittest.mock import patch\nfrom daytona import Daytona\n{setup}\npatch.multiple(client, create=fake, get=other)\n"
    # When evaluating the receiver proof for every keyword independently.
    result = migrate(source)
    # Then no partial proof can move the shared target.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


def test_multiple_is_idempotent_when_mixed_target_has_fixme() -> None:
    # Given a previously reported patch that must still be split by hand.
    source = "from unittest.mock import patch\nimport app\npatch.multiple(app.client, get=fake, close=other)\n"
    previous = migrate(source)
    # When migration is rerun on the commented source.
    result = migrate(previous.source)
    # Then the comment is not duplicated and the review item persists.
    assert result.source == previous.source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "options",
    ["spec=True", "create=True", "spec_set=True", "autospec=True", "new_callable=dict"],
)
def test_multiple_options_are_not_treated_as_members(options: str) -> None:
    # Given patch-wide options alongside moved members on a proven client.
    source = (
        "import app\nfrom unittest.mock import patch\n"
        f"patch.multiple(app.client, {options}, get=1, delete=2)\n"
    )
    # When
    result = migrate(source)
    # Then the options configure the patch and the target still moves.
    assert f"patch.multiple(app.client.v0, {options}, get=1, delete=2)" in (
        result.source
    )
