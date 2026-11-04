# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class import, field and injected-parameter proofs retain their exact meaning."""

from __future__ import annotations

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def test_routes_field_when_local_import_names_indexed_submodule() -> None:
    # Given an indexed submodule not re-exported by its package.
    sources = {
        "pkg": "",
        "pkg.sdk": "from daytona import Daytona\n",
        "app": "class Service:\n    def __init__(self):\n"
        "        from pkg import sdk\n        self.client = sdk.Daytona()\n"
        "    def run(self): return self.client.create()\n",
    }
    mapping = load_mapping()
    # When collecting field writes across methods.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then the local import supplies the same module proof as a top-level import.
    assert "self.client.v0.create()" in result.source
    assert result.items == ()


def test_preserves_ordinary_field_when_instance_parameter_is_review_only() -> None:
    # Given a widened project-instance parameter with an ordinary field.
    source = (
        "class Service:\n    def __init__(self): self.value = {}\n"
        "class Other:\n    def __init__(self): self.value = {}\n"
        "def run(service: Service = Other()): return service.value.get('key')\n"
    )
    # When reading a field from a review-only instance.
    result = migrate(source)
    # Then non-client is not mistaken for client evidence.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "dependency", ["Depends(factory)", "Depends(dependency=factory)"]
)
def test_routes_field_when_dependency_factory_returns_annotated_instance(
    dependency: str,
) -> None:
    # Given FastAPI injection of the precise annotated project type.
    source = (
        "from fastapi import Depends\nfrom daytona import Daytona\n"
        "class Service:\n    client = Daytona()\n"
        "def factory() -> Service: return Service()\n"
        f"def run(service: Service = {dependency}): return service.client.create()\n"
    )
    # When resolving the dependency before comparing its kind with the annotation.
    result = migrate(source)
    # Then matching factory evidence does not demote the client-bearing field.
    assert "service.client.v0.create()" in result.source
    assert result.items == ()


@pytest.mark.parametrize(
    "import_,decorator",
    [
        ("from functools import cached_property", "cached_property"),
        ("from functools import cached_property as cached", "cached"),
        ("import functools as ft", "ft.cached_property"),
    ],
)
def test_routes_getter_body_when_cached_property_preserves_receiver(
    import_: str,
    decorator: str,
) -> None:
    # Given a cached getter with a proven client-bearing instance receiver.
    source = (
        f"from daytona import Daytona\n{import_}\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\n"
        f"    @{decorator}\n    def sandbox(self): return self.client.create()\n"
    )
    # When migrating the getter's body, rather than only its return summary.
    result = migrate(source)
    # Then the actual call is routed through v0 for every proven import spelling.
    assert "self.client.v0.create()" in result.source
    assert result.items == ()


def test_preserves_outer_double_when_nested_parameter_shadows_its_name() -> None:
    # Given an outer non-client mock and an unrelated annotated helper parameter.
    source = (
        "from daytona import Daytona\nfrom unittest.mock import Mock\n"
        "def consume(client: Daytona): return client.create()\n"
        "def test_case():\n    fake = Mock()\n    fake.create.return_value = 42\n"
        "    def helper(fake: Daytona): return consume(fake)\n"
        "    assert fake.create() == 42\n"
    )
    # When the helper's call argument is certified independently of the outer name.
    result = migrate(source)
    # Then it cannot promote or redirect the outer mock's setup and calls.
    assert "fake.create.return_value = 42" in result.source
    assert "assert fake.create() == 42" in result.source
    assert "fake.v0.create.return_value" not in result.source


@pytest.mark.parametrize(
    "declaration,receiver",
    [
        ("    client = Daytona()\n", "service.client"),
        ("    def client(self) -> Daytona: return Daytona()\n", "service.client()"),
        (
            "    @property\n    def client(self) -> Daytona: return Daytona()\n",
            "service.client",
        ),
    ],
)
def test_reviews_client_field_when_instance_default_contradicts_annotation(
    declaration: str,
    receiver: str,
) -> None:
    # Given a review-only receiver with client-valued field/method/descriptor evidence.
    source = (
        "from daytona import Daytona\nclass Service:\n"
        + declaration
        + "class Other: pass\n"
        f"def run(service: Service = Other()): return {receiver}.create()\n"
    )
    # When exact proof matching demotes client evidence, but not unrelated values.
    result = migrate(source)
    # Then genuine client evidence still requires review rather than disappearing.
    assert any(item["kind"] == "fixme" for item in result.items)
    assert f"return {receiver}.create()" in result.source


def test_reviews_injected_field_when_dependency_returns_different_instance() -> None:
    # Given a factory contradicting the annotated holder's client field contract.
    source = (
        "from fastapi import Depends\nfrom daytona import Daytona\n"
        "class Service:\n    client = Daytona()\nclass Other: pass\n"
        "def factory() -> Other: return Other()\n"
        "def run(service: Service = Depends(factory)): return service.client.create()\n"
    )
    # When inspecting the unwrapped dependency result rather than the Depends object.
    result = migrate(source)
    # Then a conflicting result still demotes the annotation.
    assert "return service.client.create()" in result.source
    assert any(item["kind"] == "fixme" for item in result.items)
