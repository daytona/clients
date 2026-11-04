# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client evidence survives abrupt paths and nested annotation/collection boundaries."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.skipif(sys.version_info < (3, 11), reason="except* requires Python 3.11")
@pytest.mark.parametrize("raising,normal", [("Client()", "d"), ("d", "Client()")])
def test_reviews_later_group_handler_when_nested_branch_raises(
    raising: str, normal: str
) -> None:
    # Given an earlier handler with distinct abrupt and normal receiver bindings.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def run(d: Daytona, flag):\n    x = {normal}\n"
        "    try:\n        risky()\n    except* ValueError:\n"
        f"        if flag:\n            x = {raising}\n            raise RuntimeError()\n"
        f"        else:\n            x = {normal}\n"
        "    except* TypeError:\n        x.create()\n"
    )
    # When upgrading a call reached after either branch, including the raise.
    result = migrate(source)
    # Then the incompatible bindings require review rather than a false rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "setup",
    [
        "from typing import TypeAlias\nDep: TypeAlias = Daytona\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    from daytona import Daytona as Dep\n",
    ],
)
@pytest.mark.parametrize(
    "parameter,yielded", [("d: Dep", "d"), ("*values: Dep", "from values")]
)
def test_routes_generator_consumer_when_parameter_uses_type_environment(
    setup: str, parameter: str, yielded: str
) -> None:
    # Given a parameter annotation whose evidence lives outside runtime bindings.
    source = (
        f"from daytona import Daytona\n{setup}"
        f"def clients({parameter}):\n    yield {yielded}\n"
        "for value in clients(Daytona()):\n    value.create()\n"
    )
    # When upgrading the generator's consumer.
    result = migrate(source)
    # Then the full annotation environment proves the yielded client.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("async_", [False, True])
def test_routes_generator_consumer_when_vararg_elements_are_clients(
    async_: bool,
) -> None:
    # Given a variadic generator with homogeneous client element annotations.
    prefix = "async " if async_ else ""
    body = "yield values[0]" if async_ else "yield from values"
    source = (
        "from daytona import Daytona\n"
        f"{prefix}def clients(*values: Daytona):\n    {body}\n"
        f"{prefix}def run():\n    {prefix}for value in clients(Daytona()):\n"
        "        value.create()\n"
    )
    # When inspecting the yielded tuple element rather than the tuple itself.
    result = migrate(source)
    # Then variadic element evidence supports the compatibility rewrite.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("decorator", ["property", "cached_property"])
@pytest.mark.parametrize("value", ["Client()", "None", "'not a client'"])
def test_reviews_descriptor_when_annotation_conflicts_with_concrete_return(
    decorator: str, value: str
) -> None:
    # Given a synchronous descriptor whose body contradicts its client annotation.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "from functools import cached_property\n"
        f"class App:\n    @{decorator}\n"
        f"    def client(self) -> Daytona: return {value}\n"
        "app = App()\napp.client.create()\n"
    )
    # When resolving the actual descriptor read.
    result = migrate(source)
    # Then annotation evidence cannot authorize rewriting a concrete non-client.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("depth", [2, 3, 6])
def test_reviews_dictionary_value_when_zip_contains_nested_client_iterables(
    depth: int,
) -> None:
    # Given nested typed client groups used as dictionary values.
    annotation = "list[" * depth + "Daytona" + "]" * depth
    source = (
        "from daytona import Daytona\n"
        f"def run(names: list[str], groups: {annotation}):\n"
        "    values = dict(zip(names, groups))\n    values['a'].create()\n"
    )
    # When inspecting a dictionary lookup whose nested client shape is unresolved.
    result = migrate(source)
    # Then client evidence at any supported depth remains an explicit manual item.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "body",
    [
        "def clients(*values: str): yield from values\n"
        "for value in clients('text'): value.create()\n",
        "def run(names: list[str], groups: list[list[str]]):\n"
        "    values = dict(zip(names, groups))\n    values['a'].create()\n",
    ],
)
def test_preserves_nonclient_when_propagating_collection_evidence(body: str) -> None:
    # Given equivalent collection paths with no client elements.
    source = "from daytona import Daytona\n" + body
    # When inspecting these ordinary values.
    result = migrate(source)
    # Then propagation neither invents client evidence nor changes calls.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("value,status", [("Daytona()", 0), ("None", 1)])
def test_cli_routes_or_reviews_when_descriptor_result_is_known(
    tmp_path: Path, value: str, status: int
) -> None:
    # Given an installed project whose property has a concrete result.
    source = (
        "from daytona import Daytona\nclass App:\n    @property\n"
        f"    def client(self) -> Daytona: return {value}\n"
        "app = App()\napp.client.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    # When invoking the real strict upgrade surface.
    actual = main([str(tmp_path), "--force", "--yes", "--strict", "--json"])
    # Then strict status and executable output reflect proof versus conflict.
    assert actual == status
    expected = (
        source.replace("client.create()", "client.v0.create()")
        if status == 0
        else source
    )
    assert ast.dump(ast.parse(app.read_text())) == ast.dump(ast.parse(expected))
