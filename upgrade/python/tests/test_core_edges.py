# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Additional syntax-boundary regressions, independent of formatting fixtures."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "source",
    [
        "from daytona import Daytona\ndef main():\n client = make()\n client.create()\ndef make() -> Daytona:\n return Daytona()\n",
        "from daytona import Daytona\nclass Owner:\n def run(self):\n  self.client.create()\n def __init__(self, client: Daytona):\n  self.client = client\n",
        "from daytona import Daytona\nclass Owner:\n def factory(self) -> Daytona:\n  return Daytona()\n def run(self):\n  self.factory().create()\n",
    ],
)
def test_client_calls_are_namespaced_when_factory_or_self_attribute_is_proven(
    source: str,
) -> None:
    # Given a client established through the project's annotation contract.
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    # When the real codemod runs.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then its create receiver goes through v0, with valid syntax and no FIXME.
    calls = [
        node
        for node in ast.walk(ast.parse(result.source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create"
    ]
    assert len(calls) == 1
    assert isinstance(calls[0].func, ast.Attribute)
    assert isinstance(calls[0].func.value, ast.Attribute)
    assert calls[0].func.value.attr == "v0"
    assert not result.items


def test_namespace_types_move_when_used_in_annotations() -> None:
    # Given a module alias and a resource type in an annotation.
    source = "import daytona as sdk\nresource: sdk.Sandbox\n"
    mapping = load_mapping()
    context = SourceContext("app.pyi", "app", build_index({"app": source}, mapping))
    # When the annotation is migrated.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then it points at the resource namespace, not the root client package.
    assert result.source == "import daytona as sdk\nresource: sdk.v0.Sandbox\n"


def test_suggestion_removal_works_when_python_cannot_parse() -> None:
    # Given an unfinished edit with existing generated comments.
    source = "# daytona-v2: old hint\nunfinished = (\n"
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({}, mapping))
    # When only suggestion removal is requested.
    result = transform_source(
        source, TransformOptions(mapping, "remove-suggestions"), context
    )
    # Then removal does not depend on Python syntax validity.
    assert result.source == "unfinished = (\n"


@pytest.mark.parametrize(
    "annotation", ["Daytona | str", "Union[Daytona, str]", "list[Daytona]"]
)
def test_receiver_stays_untouched_when_annotation_does_not_prove_a_client(
    annotation: str,
) -> None:
    # Given a receiver which may be an unrelated value or a container.
    source = f"from daytona import Daytona\nfrom typing import Union\ndef run(value: {annotation}):\n value.create()\n"
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    # When the codemod must decide whether the receiver itself is a client.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then it makes no unjustified code edit.
    assert result.source == source


@pytest.mark.parametrize(
    "body",
    [
        "for _ in unknown:\n value = Daytona()\nvalue.create()\n",
        "value = Daytona()\nvalue += unknown\nvalue.create()\n",
    ],
)
def test_binding_is_not_proven_when_loop_may_not_run_or_value_is_modified(
    body: str,
) -> None:
    # Given a conditional or modified client binding.
    source = "from daytona import Daytona\n" + body
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    # When receiver proofs reach the later call.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then an unproven value is never mechanically redirected.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_custom_url_is_reported_when_config_is_a_function_default() -> None:
    # Given config evaluated at function definition, not inside its body.
    source = "from daytona import DaytonaConfig\ndef run(config=DaytonaConfig(api_url='https://private.example/api')):\n pass\n"
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    # When calculating the migration preflight.
    result = transform_source(source, TransformOptions(mapping), context)
    # Then the custom URL is a refusal location even if run is never called.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("custom-api-url", 2)
    ]


def test_code_is_migrated_when_notebook_uses_a_python_cell_magic(
    tmp_path: Path,
) -> None:
    # Given Python statements wrapped by a timing magic.
    notebook = tmp_path / "timed.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": [
                            "%%time\n",
                            "from daytona import Daytona\n",
                            "api = Daytona()\n",
                            "api.create()\n",
                        ],
                    }
                ]
            }
        )
    )
    # When the real CLI upgrades the notebook.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then only Python SDK code changes; the magic survives.
    assert status == 0
    assert json.loads(notebook.read_text())["cells"][0]["source"] == [
        "%%time\n",
        "from daytona import Daytona\n",
        "api = Daytona()\n",
        "api.v0.create()\n",
    ]
