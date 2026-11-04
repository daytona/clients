# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dataclass, import, fixture and mock provenance obey proof boundaries."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from upgrade_helpers import migrate_test_module as migrate

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import Resolver
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("options", ["frozen=True", "init=True", "slots=True"])
def test_factory_proves_client_when_dataclass_decorator_has_options(
    options: str,
) -> None:
    # Given a generated initializer with an SDK default factory.
    source = f"from dataclasses import dataclass, field\nfrom daytona import Daytona\n@dataclass({options})\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\nWorker().client.create()\n"
    # When inspecting a default-constructed instance.
    result = migrate(source)
    # Then decorator options do not discard its construction proof.
    assert result.source == source.replace(".client.create()", ".client.v0.create()")
    assert result.items == ()


def test_factory_stays_unproven_when_dataclass_disables_init() -> None:
    # Given a dataclass that will not run the default factory.
    source = "from dataclasses import dataclass, field\nfrom daytona import Daytona\n@dataclass(init=False)\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\nWorker().client.create()\n"
    # When inspecting a constructor call.
    result = migrate(source)
    # Then no default-initializer proof is invented.
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize("alias", ["client", "client as imported_client"])
@pytest.mark.parametrize("provider", ["app", "bridge"])
def test_mock_review_retains_history_when_client_is_imported(
    alias: str, provider: str
) -> None:
    # Given a named or aliased import of an exported, replaced client.
    name = alias.split(" as ")[-1]
    source = f'from unittest.mock import patch\nfrom {provider} import {alias}\npatch.object({name}, "create")\n'
    sources = {
        "app": "from daytona import Daytona\nclient = Daytona()\nclient = object()\n",
        "bridge": "from app import client\n",
        "test_app": source,
    }
    mapping = load_mapping()
    context = SourceContext("test_app.py", "test_app", build_index(sources, mapping))
    # When the mock target crosses a named-import boundary.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then historical evidence remains review-only, even across a re-export.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize(
    "bridge",
    ["from app import *\n", "from app import *\n__all__ = ['client']\n"],
)
@pytest.mark.parametrize("consumer", ["from app import *", "from bridge import *"])
def test_mock_review_retains_history_through_star_imports(
    bridge: str, consumer: str
) -> None:
    # Given a replaced client re-exported through wildcard imports.
    source = (
        f'from unittest.mock import patch\n{consumer}\npatch.object(client, "create")\n'
    )
    sources = {
        "app": "from daytona import Daytona\nclient = Daytona()\nclient = object()\n",
        "bridge": bridge,
        "test_app": source,
    }
    mapping = load_mapping()
    context = SourceContext("test_app.py", "test_app", build_index(sources, mapping))
    # When the mock target arrives through a star import.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then the historical evidence still requires review.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]


@pytest.mark.parametrize("fixture", ["mocker", "monkeypatch"])
@pytest.mark.parametrize("location", ["conftest", "test_app"])
def test_helper_stays_user_owned_when_project_fixture_overrides_framework(
    fixture: str, location: str
) -> None:
    # Given a visible project fixture with the framework fixture's name.
    declaration = (
        f"import pytest\n@pytest.fixture\ndef {fixture}():\n    return object()\n"
    )
    operation = (
        'mocker.patch("daytona.Sandbox")'
        if fixture == "mocker"
        else 'monkeypatch.setattr("daytona.Sandbox", fake)'
    )
    source = f"import daytona\ndef test_example({fixture}):\n    {operation}\n"
    sources = {"test_app": source}
    if location == "test_app":
        source = declaration + source
        sources[location] = source
    else:
        sources[location] = declaration
    mapping = load_mapping()
    context = SourceContext("test_app.py", "test_app", build_index(sources, mapping))
    # When applying pytest's existing fixture precedence.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then an unrelated project object is not treated as a mock helper.
    assert result.source == source
    assert result.items == ()


def test_factory_field_stays_unproven_when_call_only_returns_owning_class() -> None:
    # Given a zero-argument factory overriding the dataclass's client default.
    source = "from dataclasses import dataclass, field\nfrom daytona import Daytona\nclass Other: pass\n@dataclass\nclass Worker:\n    client: Daytona = field(default_factory=Daytona)\ndef build_worker() -> Worker:\n    return Worker(client=Other())\nbuild_worker().client.create()\n"
    # When a return annotation identifies the instance but not its initialization.
    result = migrate(source)
    # Then the default factory cannot certify the overridden field.
    assert ".v0.create()" not in result.source


def test_field_inference_requires_constructor_identity_when_result_is_instance() -> (
    None
):
    # Given an instance-returning callable with no guarantee of default field values.
    mapping = load_mapping()
    resolver = Resolver(mapping, build_index({}, mapping))
    env = {
        "Worker": "class:Worker",
        "Worker.@factory.client": "client",
        "Worker.client": "unproven:client",
        "build_worker": "fn:instance:Worker",
    }
    expression = ast.parse("build_worker().client", mode="eval").body
    # When resolving the field through that factory rather than the constructor.
    kind = resolver.infer(expression, env)
    # Then the result type is insufficient to prove the default ran.
    assert kind == "unproven:client"


@pytest.mark.parametrize("constructor", ["Worker", "Alias", "models.Worker"])
def test_factory_field_proves_client_when_callee_resolves_to_owning_class(
    constructor: str,
) -> None:
    # Given a name or attribute resolving directly to the owning class.
    mapping = load_mapping()
    resolver = Resolver(mapping, build_index({}, mapping))
    env = {
        constructor: "class:Worker:metadata",
        "Worker.@factory.client": "client",
        "Worker.client": "unproven:client",
    }
    expression = ast.parse(f"{constructor}().client", mode="eval").body
    # When resolving a default-constructed instance's field.
    kind = resolver.infer(expression, env)
    # Then direct class aliases retain the default-factory proof.
    assert kind == "client"


@pytest.mark.parametrize("target", ["daytona.Sandbox", "daytona_sdk.Sandbox.delete"])
def test_strict_reports_stale_sdk_path_when_patch_is_unbound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    # Given a stale SDK target without a proven mock-helper import.
    source = f'import daytona\npatch("{target}")\n'
    (tmp_path / "test_app.py").write_text(source)
    # When running the actual CLI in strict, read-only mode.
    status = main([str(tmp_path), "--strict", "--dry", "--json"])
    # Then the stale path blocks strict mode without a speculative rewrite.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert [item["kind"] for item in report["items"]] == ["mock-target"]
    assert report["changedFiles"] == []


@pytest.mark.parametrize("binding", ["", "def patch(target): return target\n"])
def test_patch_stays_clean_when_target_is_not_sdk_path(binding: str) -> None:
    # Given ordinary string data passed to an unbound or user-owned patch.
    source = f'import daytona\n{binding}patch("app.Widget")\n'
    # When checking for stale mock targets.
    result = migrate(source)
    # Then neither a rewrite nor a review item is warranted.
    assert result.source == source
    assert result.items == ()
