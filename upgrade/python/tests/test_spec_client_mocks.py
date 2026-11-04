# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Spec-backed mocks retain client evidence without trusting similarly named helpers."""

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
        SourceContext(
            "test_app.py", "test_app", build_index({"test_app": source}, mapping)
        ),
    )


@pytest.mark.parametrize("client_type", ["Daytona"])
@pytest.mark.parametrize(
    "construction",
    [
        "MagicMock(spec=Client)",
        "MagicMock(spec_set=Client)",
        "MagicMock(Client)",
        "Mock(spec=Client)",
        "create_autospec(Client)",
        "create_autospec(spec=Client, instance=True)",
        "m.MagicMock(spec=Client)",
        "autospec(Client)",
    ],
)
def test_routes_mock_members_when_spec_proves_client(
    construction: str, client_type: str
) -> None:
    # Given mock-library bindings and an aliased SDK class.
    source = (
        "from unittest.mock import MagicMock, Mock, create_autospec\n"
        "from unittest.mock import create_autospec as autospec\n"
        "import unittest.mock as m\n"
        f"from daytona import {client_type} as Client\n"
        f"client = {construction}\n"
        "client.create.return_value = sentinel\n"
        "client.create.assert_called_once()\n"
    )
    # When migrating configured method mocks and their assertions.
    result = migrate(source)
    # Then both address the compatibility client's mock, with no review debt.
    assert result.source == source.replace("client.create", "client.v0.create")
    assert result.items == ()


@pytest.mark.parametrize(
    "setup,construction",
    [
        ("", "MagicMock()"),
        ("class Foreign: pass\n", "MagicMock(spec=Foreign)"),
        ("class Client: pass\n", "MagicMock(spec=Client)"),
        ("def MagicMock(**kwargs): pass\n", "MagicMock(spec=Client)"),
        ("def create_autospec(spec): pass\n", "create_autospec(Client)"),
        ("from foreign import MagicMock\n", "MagicMock(spec=Client)"),
    ],
)
def test_leaves_unproven_mock_members_when_bindings_are_unrelated(
    setup: str, construction: str
) -> None:
    # Given no reliable mock-library plus Daytona-class pair.
    source = (
        "from unittest.mock import MagicMock, create_autospec\n"
        "from daytona import Daytona as Client\n"
        f"{setup}client = {construction}\nclient.create.return_value = sentinel\n"
    )
    # When migrating without name-based guesses.
    result = migrate(source)
    # Then user-owned mock shapes stay untouched and unreported.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "construction",
    [
        "MagicMock(spec=Daytona, **options)",
        "MagicMock(spec=Daytona, spec_set=unknown)",
        "MagicMock(autospec=Daytona)",
        "MagicMock(spec=Daytona, create=custom)",
        "create_autospec(Daytona, instance=unknown)",
        "patch('app.Daytona', autospec=Daytona)",
        "patch('unresolved.Client', spec=Daytona)",
        "patch.object(Daytona, 'create', autospec=Daytona)",
    ],
)
def test_reports_mock_target_when_class_is_known_but_shape_is_uncertain(
    construction: str,
) -> None:
    # Given direct class evidence combined with unresolved mock configuration.
    source = (
        "from unittest.mock import MagicMock, create_autospec, patch\n"
        "from daytona import Daytona\n"
        f"client = {construction}\nclient.create.return_value = sentinel\n"
    )
    # When migration cannot safely select the mock receiver.
    result = migrate(source)
    # Then strict review is required, rather than a guessed rewrite or silence.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("target", ["Daytona", "type(client)"])
def test_explains_v0_member_when_patch_object_targets_client_class(target: str) -> None:
    # Given a moved member patched on a proven class or an instance's class.
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona\n"
        f"client = Daytona()\npatch.object({target}, 'create')\n"
    )
    # When class-level migration cannot manufacture a v0 instance.
    result = migrate(source)
    # Then the unchanged target receives actionable moved-member guidance.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
    assert "v0 client" in result.items[0]["message"]


@pytest.mark.parametrize("setup", ["type = factory\n", "client = object()\n"])
def test_leaves_type_patch_when_builtin_or_client_is_unproven(setup: str) -> None:
    # Given a shadowed type helper or a non-client value.
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona\n"
        f"client = Daytona()\n{setup}patch.object(type(client), 'create')\n"
    )
    # When resolving the class expression.
    result = migrate(source)
    # Then the upgrader does not infer a Daytona class from the spelling alone.
    assert result.source == source
    assert result.items == ()


def test_strict_fails_when_spec_mock_shape_is_uncertain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a project with a known client class but unresolved mock options.
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "test_app.py").write_text(
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona, **options)\n"
        "client.create.return_value = sentinel\n"
    )
    # When strict CLI review evaluates the project.
    status = main([str(tmp_path), "--strict", "--json"])
    # Then uncertain mock setup cannot pass silently.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["mock-target"]


def test_routes_autospec_instance_when_class_mock_returns_client() -> None:
    # Given a class autospec and aliases of both its constructor and instance.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\n"
        "client = constructor.return_value\nalias = constructor()\n"
        "client.create.return_value = first\nalias.create.return_value = second\n"
    )
    # When migrating instance configuration reached through the class mock.
    result = migrate(source)
    # Then both proven instances receive their own compatibility namespace.
    assert result.source == source.replace("client.create", "client.v0.create").replace(
        "alias.create", "alias.v0.create"
    )
    assert result.items == ()


def test_stays_idempotent_when_spec_mock_is_already_migrated() -> None:
    # Given already-migrated mock configuration.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "client = MagicMock(spec=Daytona)\nclient.v0.create.return_value = sentinel\n"
    )
    # When the upgrader runs again.
    result = migrate(source)
    # Then no duplicate namespace or review debt is introduced.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "helper", ["AsyncMock", "NonCallableMock", "NonCallableMagicMock"]
)
def test_reports_class_evidence_when_mock_factory_has_unsupported_shape(
    helper: str,
) -> None:
    # Given a known mock factory whose call shape is not certified by this upgrader.
    source = (
        f"from unittest.mock import {helper}\nfrom daytona import Daytona\n"
        f"client = {helper}(spec=Daytona)\nclient.create.return_value = sentinel\n"
    )
    # When direct class evidence cannot establish a supported mock shape.
    result = migrate(source)
    # Then the shape is reviewed, not silently accepted or guessed.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "type_import,expression",
    [
        ("from builtins import type as class_of\n", "class_of(client)"),
        ("import builtins\n", "builtins.type(client)"),
    ],
)
def test_reports_class_patch_when_builtin_type_is_imported_under_alias(
    type_import: str, expression: str
) -> None:
    # Given a binding-proven alias of the builtin type operation.
    source = (
        "from unittest.mock import patch\nfrom daytona import Daytona\n"
        f"{type_import}client = Daytona()\npatch.object({expression}, 'create')\n"
    )
    # When reviewing the resulting Daytona class target.
    result = migrate(source)
    # Then class aliases require the same manual v0 patch adjustment.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("receiver", ["constructor", "alias"])
@pytest.mark.parametrize("replacement", ["Foreign()", "unknown()", "42"])
def test_respects_foreign_return_value_when_autospec_constructor_is_mutated(
    receiver: str,
    replacement: str,
) -> None:
    # Given a class mock whose default instance has been replaced, possibly via alias.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "class Foreign:\n    def create(self): pass\n"
        "constructor = create_autospec(Daytona)\nalias = constructor\n"
        "constructor.create.return_value = initial\n"
        f"{receiver}.return_value = {replacement}\n"
        "constructor.create.return_value = later\n"
        "constructor.return_value.create()\nresult = constructor()\nresult.create()\n"
    )
    # When the result no longer has a Daytona instance proof.
    result = migrate(source)
    # Then only the original direct class-mock configuration is routed.
    assert result.source == source.replace(
        "constructor.create.return_value", "constructor.v0.create.return_value"
    )


@pytest.mark.parametrize("receiver", ["constructor", "alias"])
@pytest.mark.parametrize("effect", ["Foreign", "lambda: Foreign()", "[Foreign()]"])
def test_reviews_result_when_autospec_constructor_side_effect_is_mutated(
    receiver: str,
    effect: str,
) -> None:
    # Given a side effect that replaces the autospec constructor's runtime result.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "class Foreign:\n    def create(self): pass\n"
        "constructor = create_autospec(Daytona)\nalias = constructor\n"
        "constructor.create.return_value = initial\n"
        f"{receiver}.side_effect = {effect}\n"
        "constructor.create.return_value = later\n"
        "result = constructor()\nresult.create()\n"
    )
    # When the effect cannot certify a Daytona runtime result.
    result = migrate(source)
    # Then the original class member routes, but the call result remains unproven.
    assert result.source == source.replace(
        "constructor.create.return_value", "constructor.v0.create.return_value"
    )
    assert [item["kind"] for item in result.items] == ["mock-target"]
