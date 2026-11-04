# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lost proof metadata must not silently break clients or injected doubles."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_dataclass_instances import migrate
from test_double_runtime import migrate_project

from daytona_upgrade.annotation_proofs import AnnotationProofs
from daytona_upgrade.classflow import AttributeWrites
from daytona_upgrade.client_parameters import ClientArguments, parameter_contract
from daytona_upgrade.flow import build_index
from daytona_upgrade.generated_parameters import generated_parameters
from daytona_upgrade.models import load_mapping
from daytona_upgrade.resolution import Resolver


def test_marks_constructor_slot_uncertain_when_pydantic_client_alias_is_dynamic() -> (
    None
):
    # Given a client field whose accepted keyword cannot be resolved statically.
    node = ast.parse(
        "class Service:\n    client: Daytona = Field(alias=alias_name)\n"
    ).body[0]
    assert isinstance(node, ast.ClassDef)
    mapping = load_mapping()
    writes = AttributeWrites(Resolver(mapping, build_index({}, mapping)), {})
    writes.field_framework = "pydantic"
    writes.class_lexical = {"Field": "module:pydantic.Field", "Daytona": "type:client"}
    # When extracting the generated constructor contract.
    slots = generated_parameters(node, writes)
    # Then any constructor keyword may supply this unresolved client slot.
    assert slots == {"client": "*=any"}


@pytest.mark.parametrize("framework", ["pydantic", "attrs"])
def test_reviews_injection_when_field_alias_is_dynamic(
    tmp_path: Path, framework: str
) -> None:
    # Given a runtime alias that makes the generated signature uncertain.
    definition = (
        "from pydantic import BaseModel, Field\n"
        "class Service(BaseModel):\n"
        "    label: str = Field(default='label')\n"
        "    client: Daytona = Field(default_factory=Daytona, alias=alias_name)\n"
        if framework == "pydantic"
        else "from attrs import define, field\n@define\nclass Service:\n"
        "    label: str = field(default='label', kw_only=True)\n"
        "    client: Daytona = field(factory=Daytona, alias=alias_name, kw_only=True)\n"
    )
    sources = {
        "provider": "from daytona import Daytona\nalias_name = 'connection'\n"
        + definition
        + "    def run(self): return self.client.create()\n",
        "app": "from types import SimpleNamespace\nfrom provider import Service\n"
        "fake = SimpleNamespace(create=lambda: 73)\nService(connection=fake).run()\n",
    }
    # When migrating a method that reads the injected field.
    results = migrate_project(sources, tmp_path)
    # Then the fake injected under the client's dynamic alias is never broken silently:
    # either the consumer stays on review (FIXME) or the injection is reported.
    provider_rewritten = "self.client.v0.create()" in results["provider"].source
    reported = any(item["kind"] == "mock-target" for item in results["app"].items)
    reviewed = any(item["kind"] == "fixme" for item in results["provider"].items)
    assert reported if provider_rewritten else reviewed
    assert ast.dump(ast.parse(results["app"].source)) == ast.dump(
        ast.parse(sources["app"])
    )


@pytest.mark.parametrize("method", ["__init__", "reset"])
def test_routes_stored_client_when_method_varargs_have_element_annotation(
    method: str,
) -> None:
    # Given a typed variadic input stored for use by another instance method.
    source = (
        "from daytona import Daytona\nclass Service:\n"
        f"    def {method}(self, *clients: Daytona):\n"
        "        self.client = clients[0]\n"
        "    def run(self): return self.client.create()\n"
    )
    # When class-wide stores resolve the variadic element.
    result = migrate(source)
    # Then the later method receives the same proof as an ordinary function.
    assert "self.client.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize("replacement", ["", "        self.client = optional\n"])
def test_routes_attribute_join_only_when_annotated_store_remains_nonnull(
    replacement: str,
) -> None:
    # Given an annotated non-null attribute, optionally replaced by a nullable one.
    source = (
        "from typing import Optional\nfrom daytona import Daytona\n"
        "class Service:\n"
        "    def run(self, optional: Optional[Daytona]):\n"
        "        self.client: Daytona = Daytona()\n"
        + replacement
        + "        client = self.client and Daytona()\n"
        "        return client.create()\n"
    )
    # When joining the current attribute value with another client.
    result = migrate(source)
    # Then non-null evidence authorizes the join only until its binding changes.
    if replacement:
        assert "return client.create()" in result.source
        assert [item["kind"] for item in result.items] == ["fixme"]
    else:
        assert "return client.v0.create()" in result.source
        assert not result.items


@pytest.mark.parametrize("collection", ["list", "tuple"])
def test_retains_caller_review_contract_when_return_collection_has_mixed_elements(
    collection: str,
) -> None:
    # Given a typed client relayed inside a collection with foreign alternatives.
    annotation = (
        "list[Daytona | str]" if collection == "list" else "tuple[Daytona | str, ...]"
    )
    value = "[client]" if collection == "list" else "(client,)"
    node = ast.parse(
        f"def relay(client: Daytona) -> {annotation}: return {value}\n"
    ).body[0]
    assert isinstance(node, ast.FunctionDef)
    mapping = load_mapping()
    resolver = Resolver(mapping, build_index({}, mapping))
    # When building the same contract used by caller-side double analysis.
    contract = parameter_contract(
        node, AnnotationProofs(resolver, {"Daytona": "type:client"})
    )
    # Then the caller retains the unsafe returned parameter as review-only evidence.
    assert contract == "client=~?/0"
    call = ast.parse("relay(fake)", mode="eval").body
    assert isinstance(call, ast.Call)
    assert ClientArguments(resolver, {"relay.@client-parameters": contract}).arguments(
        call, include_relays=True
    ) == {call.args[0]}
