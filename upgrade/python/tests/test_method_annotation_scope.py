# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Local-double contracts respect class-suite annotation bindings."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("annotation", ["Daytona", "'Daytona'"])
@pytest.mark.parametrize("imported", [False, True])
def test_preserves_double_when_method_annotation_has_class_shadow(
    annotation: str, imported: bool
) -> None:
    # Given a class binding shadowing the module's SDK constructor at definition.
    service = (
        "from daytona import Daytona\nclass Svc:\n    Daytona = OtherThing\n"
        f"    def run(self, c: {annotation}): c.create()\n"
    )
    source = (
        ("from provider import Svc\n" if imported else service)
        + "from unittest.mock import MagicMock\ndef test_run():\n"
        "    double = MagicMock()\n    double.create.return_value = value\n"
        "    Svc().run(double)\n    double.create.assert_called_once()\n"
    )
    sources = {"test_app": source}
    if imported:
        sources["provider"] = service
    mapping = load_mapping()
    # When exporting and consuming bound method parameter contracts.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("test_app.py", "test_app", build_index(sources, mapping)),
    )
    # Then an unrelated argument never acquires a client namespace from spelling.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "v0"
        for node in ast.walk(ast.parse(result.source))
    )
