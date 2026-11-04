# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor mock mutations and class patch targets never prove an instance."""

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


@pytest.mark.parametrize("receiver", ["constructor", "alias"])
@pytest.mark.parametrize(
    "configuration",
    [
        "return_value=Foreign()",
        "side_effect=Foreign",
        "side_effect=lambda: Foreign()",
        "side_effect=[Foreign()]",
        '**{"return_value": Foreign()}',
        '**{"side_effect": Foreign}',
        "**options",
    ],
)
@pytest.mark.parametrize("prior", ["", "constructor.return_value = Daytona()\n"])
def test_reviews_result_when_configure_mock_can_replace_constructor_result(
    receiver: str, configuration: str, prior: str
) -> None:
    # Given a class autospec configured through its binding or an alias.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "class Foreign:\n    def create(self): pass\n"
        "constructor = create_autospec(Daytona)\nalias = constructor\n"
        f"{prior}{receiver}.configure_mock({configuration})\n"
        "result = constructor()\nresult.create()\n"
    )
    # When the mock's result can no longer be certified as a Daytona instance.
    result = migrate(source)
    # Then the foreign result is untouched and the constructor call needs review.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "configuration", ["", "name='renamed'", '**{"create.return_value": 42}']
)
def test_routes_instance_when_configuration_preserves_constructor_result(
    configuration: str,
) -> None:
    # Given configuration unrelated to the constructor's return shape.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\n"
        f"constructor.configure_mock({configuration})\n"
        "result = constructor()\nresult.create()\n"
    )
    # When migration retains the constructor's default instance proof.
    result = migrate(source)
    # Then only the constructed instance gets the compatibility namespace.
    assert result.source == source.replace("result.create()", "result.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("member", ["return_value", "side_effect"])
def test_retains_existing_instance_when_constructor_is_configured_later(
    member: str,
) -> None:
    # Given an instance captured before configuration changes future call results.
    source = (
        "from unittest.mock import create_autospec\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\nexisting = constructor()\n"
        f"constructor.configure_mock({member}=foreign)\n"
        "existing.create()\nresult = constructor()\nresult.create()\n"
    )
    # When the result proof is invalidated in evaluation order.
    result = migrate(source)
    # Then only the already-captured instance remains eligible for rewriting.
    assert result.source == source.replace("existing.create()", "existing.v0.create()")
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "operation",
    [
        "constructor.configure_mock(return_value=foreign)\nresult = constructor()\nresult.create()",
        "patch.object(constructor, 'create')",
    ],
)
def test_strict_cli_requires_review_when_constructor_mock_proof_is_unsafe(
    operation: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an already-upgraded project with either unsafe mock shape.
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    (tmp_path / "test_app.py").write_text(
        "from unittest.mock import create_autospec, patch\nfrom daytona import Daytona\n"
        f"constructor = create_autospec(Daytona)\n{operation}\n"
    )
    # When strict review drives the public CLI.
    status = main([str(tmp_path), "--strict", "--json"])
    # Then the unresolved mock prevents a successful strict exit.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["mock-target"]


@pytest.mark.parametrize("target", ["constructor", "alias"])
@pytest.mark.parametrize(
    "patch_call",
    [
        "patch.object({target}, 'create')",
        "patch.object(target={target}, attribute='create')",
        "patch.multiple({target}, create=replacement)",
    ],
)
def test_reviews_patch_when_target_is_class_autospec(
    target: str, patch_call: str
) -> None:
    # Given a class mock, rather than its constructed instance, as a patch target.
    source = (
        "from unittest.mock import create_autospec, patch\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\nalias = constructor\n"
        f"{patch_call.format(target=target)}\n"
    )
    # When the upgrader resolves the moved member's patch receiver.
    result = migrate(source)
    # Then it requests review without inventing a class-level v0 namespace.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("target", ["constructor.return_value", "constructor()"])
def test_routes_patch_when_target_is_constructed_mock_instance(target: str) -> None:
    # Given the actual default instance of an unmodified class autospec.
    source = (
        "from unittest.mock import create_autospec, patch\nfrom daytona import Daytona\n"
        "constructor = create_autospec(Daytona)\n"
        f"patch.object({target}, 'create')\n"
    )
    # When the instance's moved member is patched.
    result = migrate(source)
    # Then the compatibility namespace belongs on that instance, not its class.
    assert result.source == source.replace(
        f"patch.object({target},", f"patch.object({target}.v0,"
    )
    assert result.items == ()
