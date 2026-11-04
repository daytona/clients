# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Construction hooks cannot certify a client result from a subclass call."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "declarations",
    [
        "class Custom(Daytona):\n    def __new__(cls): return Other()",
        "class Parent(Daytona):\n    def __new__(cls): return Other()\nclass Custom(Parent): pass",
        "class Mixin:\n    def __new__(cls): return Other()\nclass Custom(Daytona, Mixin): pass",
        "class Meta(type):\n    def __call__(cls): return Other()\nclass Custom(Daytona, metaclass=Meta): pass",
        "class Meta(type): pass\nclass Parent(Daytona, metaclass=Meta): pass\nclass Custom(Parent): pass",
        "from external import Meta\nclass Custom(Daytona, metaclass=Meta): pass",
        "def replace(cls): return lambda: Other()\n@replace\nclass Custom(Daytona): pass",
        "def replace(cls): return lambda: Other()\n@replace\nclass Parent(Daytona): pass\nclass Custom(Parent): pass",
        "from dataclasses import dataclass\n@dataclass\nclass Custom(Daytona): pass",
        "from dataclasses import dataclass\n@dataclass(slots=True)\nclass Custom(Daytona): pass",
        "from abc import ABCMeta\nABCMeta = replace\nclass Custom(Daytona, metaclass=ABCMeta): pass",
    ],
)
@pytest.mark.parametrize(
    "access", ["Custom().create()", "getattr(Custom(), 'create')()"]
)
def test_call_is_manual_when_project_construction_may_replace_result(
    declarations: str, access: str
) -> None:
    # Given a class binding whose construction can return a non-client.
    source = f"from daytona import Daytona\n{declarations}\n{access}\n"
    # When migrating its call result.
    result = migrate(source)
    # Then executable code stays intact and the uncertainty is reported.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "declarations",
    [
        "class Custom(Daytona): pass",
        "class Custom(Daytona):\n    def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs)",
        "import abc\nclass Custom(Daytona, metaclass=abc.ABCMeta): pass",
        "from abc import ABCMeta as Meta\nclass Custom(Daytona, metaclass=Meta): pass",
        "from abc import ABCMeta\nclass Parent(Daytona, metaclass=ABCMeta): pass\nclass Custom(Parent): pass",
    ],
)
def test_call_routes_when_construction_is_ordinary_or_abcmeta(
    declarations: str,
) -> None:
    # Given an ordinary or binding-proven ABCMeta subclass.
    source = f"from daytona import Daytona\n{declarations}\nCustom().create()\n"
    # When migrating its inherited SDK member.
    result = migrate(source)
    # Then the compatibility rewrite remains automatic.
    assert result.source == source.replace("Custom().create", "Custom().v0.create")
    assert result.items == ()


@pytest.mark.parametrize("hook", ["new", "metaclass", "decorator"])
def test_call_is_manual_when_construction_effect_is_imported(hook: str) -> None:
    # Given an unsafe parent exported through an intermediate module.
    declarations = {
        "new": "class Parent(Daytona):\n    def __new__(cls): return Other()",
        "metaclass": "class Meta(type): pass\nclass Parent(Daytona, metaclass=Meta): pass",
        "decorator": "@replace\nclass Parent(Daytona): pass",
    }
    sources = {
        "base": "from daytona import Daytona\n" + declarations[hook] + "\n",
        "bridge": "from base import Parent\nclass Custom(Parent): pass\n",
        "app": "from bridge import Custom\nCustom().create()\n",
    }
    mapping = load_mapping()
    # When resolving a cross-module constructor result.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then import indexing cannot restore a disproven construction proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources["app"]))
    assert [item["kind"] for item in result.items] == ["fixme"]
