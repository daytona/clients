# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client-parameter contracts preserve receiver binding and mutation fences."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    ("declaration", "call", "routed"),
    [
        ("def run(self, client: Daytona): pass", "Service().run(daytona)", True),
        ("def run(self, client: Daytona): pass", "Service.run(None, daytona)", True),
        ("def run(self, client: Daytona): pass", "Service.run(daytona)", False),
        (
            "@staticmethod\n    def run(client: Daytona): pass",
            "Service().run(daytona)",
            True,
        ),
        (
            "@classmethod\n    def run(cls, client: Daytona): pass",
            "Service.run(daytona)",
            True,
        ),
        (
            "@unknown\n    def run(self, client: Daytona): pass",
            "Service().run(daytona)",
            False,
        ),
        ("def run(self, client: Daytona): pass", "Service().run(*[daytona])", False),
        (
            "def run(self, client: Daytona): pass",
            "Service().run(daytona, **options)",
            False,
        ),
        (
            "def run(self, client: Daytona): pass",
            "Service().run(daytona, client=other)",
            False,
        ),
    ],
)
def test_routes_double_only_when_call_parameter_binding_is_exact(
    declaration: str, call: str, routed: bool
) -> None:
    # Given bound, unbound, static, decorated and dynamically supplied call shapes.
    source = (
        "from daytona import Daytona\nfrom unittest.mock import MagicMock\n"
        f"class Service:\n    {declaration.replace(': pass', ': return client.create()')}\n"
        f"def test_client():\n    daytona = MagicMock()\n    daytona.create.return_value = value\n    {call}\n"
    )
    mapping = load_mapping()
    # When linking every object flow to an explicitly annotated parameter.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext(
            "test_app.py", "test_app", build_index({"test_app": source}, mapping)
        ),
    )
    # Then only calls with an exact parameter binding authorize namespace changes.
    assert (
        any(
            isinstance(node, ast.Attribute)
            and node.attr == "v0"
            and isinstance(node.value, ast.Name)
            and node.value.id == "daytona"
            for node in ast.walk(ast.parse(result.source))
        )
        is routed
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "service.run = other",
        "Service.run = other",
        "service = other",
        "Service.run = MagicMock()",
    ],
)
def test_preserves_double_when_client_consumer_is_replaced(mutation: str) -> None:
    # Given a known consumer replaced before the mock flows into it.
    source = (
        "from daytona import Daytona\nfrom unittest.mock import MagicMock\n"
        "class Service:\n    def run(self, client: Daytona): pass\n"
        f"def test_client(service: Service):\n    daytona = MagicMock()\n    daytona.create.return_value = value\n    {mutation}\n    service.run(daytona)\n"
    )
    mapping = load_mapping()
    # When resolving the live callable rather than stale class metadata.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext(
            "test_app.py", "test_app", build_index({"test_app": source}, mapping)
        ),
    )
    # Then the original annotation cannot certify the replacement's parameters.
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "v0"
        for node in ast.walk(ast.parse(result.source))
    )
