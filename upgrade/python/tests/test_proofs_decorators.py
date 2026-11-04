# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only binding-proven transparent decorators preserve factory annotations."""

from __future__ import annotations

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("decorator", ["adapt", "adapt()", "cache", "property"])
def test_factory_is_unproven_when_decorator_binding_is_unknown(decorator: str) -> None:
    # Given a decorator which may replace a client-returning function.
    source = (
        "from daytona import Daytona\n"
        "def adapt(fn): return lambda: Other()\ncache = adapt\nproperty = adapt\n"
        f"@{decorator}\ndef factory() -> Daytona: return Daytona()\n"
        "client = factory()\nclient.create()\n"
    )
    # When migrating the decorated factory's consumer.
    result = migrate(source)
    # Then the wrapped function's annotation is not a receiver proof.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "imports,decorator",
    [
        ("from functools import cache", "cache"),
        ("from functools import lru_cache as cached", "cached(maxsize=1)"),
        ("import functools as ft", "ft.lru_cache"),
        ("from functools import wraps as transparent", "transparent(original)"),
    ],
)
def test_factory_stays_proven_when_decorator_is_transparent(
    imports: str, decorator: str
) -> None:
    # Given a decorator imported from the small transparent allowlist.
    source = f"from daytona import Daytona\n{imports}\n@{decorator}\ndef factory() -> Daytona: return Daytona()\nclient = factory()\nclient.create()\n"
    # When migrating the factory's consumer.
    result = migrate(source)
    # Then the known decorator preserves the return proof.
    assert "client.v0.create()" in result.source


def test_property_result_is_proven_when_accessed_as_an_attribute() -> None:
    # Given a property on a real class, accessed without calling the descriptor.
    source = (
        "from daytona import Daytona\nclass Factory:\n    @property\n"
        "    def client(self) -> Daytona: return Daytona()\n"
        "client = Factory().client\nclient.create()\n"
    )
    # When inspecting executable property-style access instead of property().
    result = migrate(source)
    # Then the descriptor's annotated result proves the receiver.
    assert "client.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize("decorator", ["staticmethod", "classmethod"])
def test_method_stays_proven_when_class_decorator_is_transparent(
    decorator: str,
) -> None:
    # Given a callable class-level descriptor, not a module-level descriptor.
    source = (
        "from daytona import Daytona\nclass Factory:\n"
        f"    @{decorator}\n    def make(*args) -> Daytona: return Daytona()\n"
        "client = Factory.make()\nclient.create()\n"
    )
    # When migrating a real method invocation.
    result = migrate(source)
    # Then these built-in descriptors preserve the return proof.
    assert "client.v0.create()" in result.source


@pytest.mark.parametrize(
    "binding",
    [
        "from functools import cache\ncache = adapt",
        "import functools as ft\nft.cache = adapt\ncache = ft.cache",
        "from functools import cache as cached\ncache = cached\ncache = adapt",
        "staticmethod = adapt\ncache = staticmethod",
    ],
)
def test_factory_is_unproven_when_transparent_decorator_is_shadowed(
    binding: str,
) -> None:
    # Given an allowlisted name rebound to an arbitrary decorator.
    source = f"from daytona import Daytona\ndef adapt(fn): return lambda: Other()\n{binding}\n@cache\ndef factory() -> Daytona: return Daytona()\nclient = factory()\nclient.create()\n"
    # When resolving the decorator at its definition position.
    result = migrate(source)
    # Then spelling alone cannot preserve the factory proof.
    assert "client.v0.create()" not in result.source


@pytest.mark.parametrize("receiver", ["Factory().make()", "Factory.make()"])
def test_method_factory_is_unproven_when_decorator_is_unknown(receiver: str) -> None:
    # Given an annotated method whose decorator replaces its return behavior.
    source = f"from daytona import Daytona\ndef adapt(fn): return lambda *args: Other()\nclass Factory:\n    @adapt\n    def make(self) -> Daytona: return Daytona()\nclient = {receiver}\nclient.create()\n"
    # When collecting class-wide method proofs.
    result = migrate(source)
    # Then the class index does not restore the wrapped method's proof.
    assert "client.v0.create()" not in result.source


def test_imported_factory_is_unproven_when_decorator_is_unknown() -> None:
    # Given a decorated factory exported to another module.
    mapping = load_mapping()
    sources = {
        "factory": "from daytona import Daytona\ndef adapt(fn): return lambda: Other()\n@adapt\ndef make() -> Daytona: return Daytona()\n",
        "app": "from factory import make\nclient = make()\nclient.create()\n",
    }
    # When migrating the consumer with the complete project index.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then cross-module indexing cannot resurrect the decorated annotation.
    assert "client.v0.create()" not in result.source
