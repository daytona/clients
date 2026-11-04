# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regressions for iterable evidence and context entry result provenance."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def test_routes_property_when_annotated_getter_decorator_is_rebound_later() -> None:
    # Given a descriptor created before its decorator's enclosing binding changes.
    source = (
        "from daytona import Daytona\nclass App:\n    @property\n"
        "    def client(self) -> Daytona: return Daytona()\n"
        "property = unknown\napp = App()\napp.client.create()\n"
    )
    # When inspecting descriptor creation separately from its delayed execution.
    result = migrate(source)
    # Then execution-time invalidation cannot change the already-created descriptor.
    assert result.source == source.replace(
        "app.client.create()", "app.client.v0.create()"
    )
    assert result.items == ()


def test_routes_generator_when_cache_decorator_is_rebound_after_definition() -> None:
    # Given an already-decorated generator with a later unrelated decorator-name write.
    source = (
        "from functools import cache\nfrom daytona import Daytona\n@cache\n"
        "def clients():\n    yield Daytona()\ncache = unknown\n"
        "for value in clients():\n    value.create()\n"
    )
    # When resolving generator execution after its definition-time decoration.
    result = migrate(source)
    # Then decorator rebinding cannot remove the existing wrapper's result proof.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("value", ["http", "'text'"])
def test_preserves_dict_values_when_pair_variable_contains_nonclient(
    value: str,
) -> None:
    # Given literal pairs containing a typed foreign client or a string.
    source = (
        "from httpx import Client\ndef run(http: Client):\n"
        f"    pairs = [('a', {value})]\n    values = dict(pairs)\n"
        "    values['a'].get('url')\n"
    )
    # When migrating the copied value's call.
    result = migrate(source)
    # Then ordinary dictionary construction creates no Daytona review item.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("async_", [False, True])
def test_routes_element_when_local_generator_yields_client(async_: bool) -> None:
    # Given an unannotated generator with a directly proven yielded client.
    prefix = "async " if async_ else ""
    source = (
        "from daytona import Daytona\n"
        f"{prefix}def clients():\n    yield Daytona()\n"
        f"{prefix}def run():\n    {prefix}for value in clients():\n"
        "        value.create()\n"
    )
    # When migrating the iteration target's call.
    result = migrate(source)
    # Then generator evidence reaches its element rather than disappearing.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("module", ["typing", "collections.abc"])
@pytest.mark.parametrize("annotation", ["AsyncIterator", "AsyncIterable"])
def test_routes_element_when_async_iterable_annotation_proves_client(
    module: str, annotation: str
) -> None:
    # Given a binding-proven asynchronous iterable annotation.
    source = (
        f"from {module} import {annotation}\nfrom daytona import Daytona\n"
        f"async def run(clients: {annotation}[Daytona]):\n"
        "    async for value in clients:\n        value.create()\n"
    )
    # When migrating the asynchronous iteration target.
    result = migrate(source)
    # Then the known element type is forwarded just as for synchronous iterables.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("async_", [False, True])
def test_routes_context_target_when_nullcontext_wraps_client(async_: bool) -> None:
    # Given a stdlib identity context around a proven client.
    prefix = "async " if async_ else ""
    source = (
        "from contextlib import nullcontext\nfrom daytona import Daytona\n"
        f"{prefix}def run(d: Daytona):\n"
        f"    {prefix}with nullcontext(d) as value:\n        value.create()\n"
    )
    # When migrating the context target's call.
    result = migrate(source)
    # Then both context protocols return the original wrapped value.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("async_", [False, True])
def test_preserves_context_target_when_subclass_entry_returns_foreign(
    async_: bool,
) -> None:
    # Given a Daytona subclass whose entry protocol returns a foreign client.
    prefix, entry = ("async ", "__aenter__") if async_ else ("", "__enter__")
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"class Sub(Daytona):\n    {prefix}def {entry}(self):\n"
        "        return Client()\n"
        f"{prefix}def run():\n    {prefix}with Sub() as value:\n"
        "        value.get('url')\n"
    )
    # When migrating a member of the entry result rather than the manager.
    result = migrate(source)
    # Then a project override cannot certify the target as a Daytona instance.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert all(item["kind"] == "fixme" for item in result.items)


def test_preserves_context_target_when_project_subclass_entry_returns_foreign() -> None:
    # Given an imported subclass whose entry protocol is defined in the project.
    sources = {
        "base": "from daytona import Daytona\nfrom httpx import Client\n"
        "class Sub(Daytona):\n    def __enter__(self): return Client()\n",
        "app": "from base import Sub\nwith Sub() as value:\n    value.get('url')\n",
    }
    mapping = load_mapping()
    # When migrating the consumer through the indexed class hierarchy.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then imported overrides also prevent a guessed compatibility rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources["app"]))
    assert all(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "body",
    [
        "d = Daytona()\n    yield d",
        "yield from [Daytona()]",
        "yield Daytona()\n    yield Daytona()",
    ],
)
def test_routes_element_when_generator_has_straight_line_proof(body: str) -> None:
    # Given homogeneous direct, aliased, or delegated yields.
    source = (
        f"from daytona import Daytona\ndef clients():\n    {body}\n"
        "for value in clients():\n    value.create()\n"
    )
    # When migrating a yielded element's member.
    result = migrate(source)
    # Then each supported straight-line producer retains exact client proof.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "setup,body",
    [
        ("", "yield Daytona()\n    yield 'other'"),
        ("@unknown\n", "yield Daytona()"),
        ("", "Daytona = unknown\n    yield Daytona()"),
        ("", "yield None"),
        ("", "def nested():\n        yield Daytona()\n    return []"),
    ],
)
def test_preserves_element_when_generator_lacks_exact_proof(
    setup: str, body: str
) -> None:
    # Given a mixed, decorated, shadowed, unrelated, or nested-only producer.
    source = (
        f"from daytona import Daytona\n{setup}def clients():\n    {body}\n"
        "for value in clients():\n    value.create()\n"
    )
    # When inspecting its iteration result.
    result = migrate(source)
    # Then uncertain producer evidence never certifies a compatibility rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_preserves_element_when_generator_constructor_is_rebound_later() -> None:
    # Given a deferred yield whose constructor binding changes before iteration.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def clients():\n    yield Daytona()\nDaytona = Client\n"
        "for value in clients():\n    value.get('url')\n"
    )
    # When inspecting the value produced using the replacement constructor.
    result = migrate(source)
    # Then definition-time evidence does not prove a later generator execution.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("annotation", ["AsyncIterator", "AsyncIterable"])
def test_routes_element_when_async_factory_return_annotation_proves_client(
    annotation: str,
) -> None:
    # Given an aliased asynchronous iterable type on a factory's return value.
    source = (
        f"from typing import {annotation} as Results\nfrom daytona import Daytona\n"
        "def clients() -> Results[Daytona]: return unknown()\n"
        "async def run():\n    async for value in clients():\n        value.create()\n"
    )
    # When consuming the annotated factory.
    result = migrate(source)
    # Then callable result evidence reaches the asynchronous element.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("constructor", ["nullcontext", "nullcontext(enter_result=d)"])
def test_preserves_target_when_nullcontext_binding_is_shadowed(
    constructor: str,
) -> None:
    # Given the known context spelling rebound to an arbitrary callable.
    expression = "nullcontext(d)" if constructor == "nullcontext" else constructor
    source = (
        "from contextlib import nullcontext\nfrom daytona import Daytona\n"
        "nullcontext = unknown\ndef run(d: Daytona):\n"
        f"    with {expression} as value:\n        value.create()\n"
    )
    # When migrating a value produced by the replacement context.
    result = migrate(source)
    # Then the stdlib identity contract is not assumed from spelling alone.
    assert result.source == source
    assert result.items == ()


def test_routes_target_when_nullcontext_alias_uses_keyword_value() -> None:
    # Given a module-qualified identity context saved before entry.
    source = (
        "import contextlib as ctx\nfrom daytona import Daytona\n"
        "async def run(d: Daytona):\n    manager = ctx.nullcontext(enter_result=d)\n"
        "    async with manager as value:\n        value.create()\n"
    )
    # When entering its asynchronous protocol through a local alias.
    result = migrate(source)
    # Then the keyword value, not the manager object, supplies target proof.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("pair", ["('a', d)", "(d, 'a')"])
def test_reviews_dict_pair_variable_when_literal_contains_client(pair: str) -> None:
    # Given a real client in either position of a literal pair variable.
    source = (
        "from daytona import Daytona\ndef run(d: Daytona):\n"
        f"    pairs = [{pair}]\n    values = dict(pairs)\n"
        "    for value in values.values(): value.create()\n"
    )
    # When inspecting a value whose dictionary position cannot be certified.
    result = migrate(source)
    # Then the nonclient false-positive fix does not erase actual client evidence.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "body",
    [
        "holder = unknown()\n    yield holder.api",
        "holder = Daytona()\n    @((holder := unknown()).decorate)\n"
        "    def nested(): pass\n    yield holder",
    ],
)
def test_preserves_element_when_generator_rebinds_outer_attribute_owner(
    body: str,
) -> None:
    # Given a local shadow or definition-time decorator replacing inherited proof.
    source = (
        "from daytona import Daytona\nholder = unknown()\nholder.api = Daytona()\n"
        f"def clients():\n    {body}\n"
        "for value in clients():\n    value.create()\n"
    )
    # When inspecting the generator result after the local write.
    result = migrate(source)
    # Then stale enclosing proofs cannot certify the yielded object.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
