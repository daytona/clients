# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Collection shapes and descriptor uncertainty remain linked."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("collection", ["list[Daytona]", "tuple[Daytona, ...]"])
@pytest.mark.parametrize(
    "consumer", ["make()[0].create()", "for c in make(): c.create()"]
)
def test_collection_elements_require_review_when_factory_has_conflicting_branch(
    collection: str, consumer: str
) -> None:
    # Given a collection factory whose other branch returns an opaque value.
    mapping = load_mapping()
    sources = {
        "factory": (
            "from daytona import Daytona\nif flag:\n"
            f"    def make() -> {collection}: return [Daytona()]\n"
            "else:\n    def make(): return unknown()\n"
        ),
        "app": f"from factory import make\n{consumer}\n",
    }
    # When consuming the joined export without importing the SDK directly.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then elements keep linked uncertainty without an automatic rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources["app"]))
    assert [(item["kind"], item["line"]) for item in result.items] == [("fixme", 2)]


@pytest.mark.parametrize(
    "write", ["self.c = Daytona()", "setattr(self, 'c', Daytona())"]
)
@pytest.mark.parametrize("consumer", ["self.c.create()", "Child().c.create()"])
def test_qualified_setter_requires_review_when_it_discards_client_argument(
    write: str, consumer: str
) -> None:
    # Given an inherited getter with a newly declared qualified setter.
    source = (
        "from daytona import Daytona\nclass Parent:\n"
        "    @property\n    def c(self): return unknown()\n"
        "class Child(Parent):\n    @Parent.c.setter\n"
        "    def c(self, value): pass\n"
        f"    def __init__(self):\n        {write}\n"
        + (
            f"    def run(self):\n        {consumer}\n"
            if consumer.startswith("self")
            else consumer + "\n"
        )
    )
    # When migrating a consumer of the setter-controlled value.
    result = migrate(source)
    # Then the supplied argument is evidence, never proof of the getter result.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("fixme", len(source.splitlines()))
    ]


@pytest.mark.parametrize("factory", ["daytona.make_client", "other.make_client"])
@pytest.mark.parametrize("getter", ["property", "cached_property"])
def test_branching_getter_requires_review_only_when_return_has_daytona_identity(
    factory: str, getter: str
) -> None:
    # Given a multi-statement getter with an unresolved returned call.
    source = (
        "from daytona import Daytona\nfrom functools import cached_property\n"
        f"class Owner:\n    @{getter}\n    def c(self):\n"
        f"        if flag: return {factory}()\n        return unknown()\n"
        "Owner().c.create()\n"
    )
    # When migrating the descriptor consumer.
    result = migrate(source)
    # Then explicit Daytona identity is review-only; generic factories stay clean.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == (
        [("fixme", len(source.splitlines()))] if factory.startswith("daytona") else []
    )


@pytest.mark.parametrize("linked", [False, True])
def test_empty_exact_field_keeps_review_when_wildcard_store_has_client_evidence(
    linked: bool,
) -> None:
    # Given an opaque exact field and a computed store that may replace it.
    value = "Daytona()" if linked else "unknown()"
    source = (
        "from daytona import Daytona\nclass Owner:\n"
        "    def __init__(self, name):\n        self.client = unknown()\n"
        f"        setattr(self, name, {value})\n"
        "    def run(self):\n        self.client.create()\n"
    )
    # When reading the exact field after class-wide store collection.
    result = migrate(source)
    # Then an empty binding does not mask linked wildcard uncertainty.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [(item["kind"], item["line"]) for item in result.items] == (
        [("fixme", len(source.splitlines()))] if linked else []
    )
