# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative proofs at optional-write and object boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "body",
    [
        "d = Other()\nif False and (d := Daytona()):\n    pass\nprint(d.create())",
        "d = Other()\nx = (d := Daytona()) if False else None\nprint(d.create())",
        "d = Daytona()\nx = True or (d := Other())\nprint(d.create())",
        "d = Daytona()\nx = (d := Other()) if False else None\nprint(d.create())",
        "d = Other()\nmatch 1:\n    case 0: d = Daytona()\n    case 1: pass\nprint(d.create())",
        "d = Daytona()\nmatch Other():\n    case d: print(d.create())",
        "d = Daytona()\nmatch []:\n    case [*d]: print(d.create())",
        "d = Daytona()\nmatch {}:\n    case {**d}: print(d.create())",
        "d = Daytona()\nxs = [(d := Other()) for x in values]\nprint(d.create())",
        "d = Other()\nxs = [(d := Daytona()) for x in values]\nprint(d.create())",
        "x = Holder()\nx.client = Daytona()\nx = Holder()\nprint(x.client.create())",
        "x = Holder()\nx.client = Daytona()\ndel x\nx = Holder()\nprint(x.client.create())",
        "x = Holder()\nx.client = Daytona()\nx, y = unknown\nprint(x.client.create())",
        "x = Holder()\nx.client = Daytona()\n*x, = unknown\nprint(x.client.create())",
        "x = Holder()\nx.client = Daytona()\nfor x in unknown:\n    print(x.client.create())",
        "x = Holder()\nx.client = Daytona()\nf = lambda x: x.client.create()",
        "x = Holder()\nx.client = Daytona()\ndef f(x):\n    print(x.client.create())",
        "x = Holder()\nx.client = Daytona()\nfrom elsewhere import value as x\nprint(x.client.create())",
        "class A:\n    def __init__(self): self.client = Daytona()\n    def run(self, other):\n        self = other\n        return self.client.create()",
        "class A:\n    def __init__(self): self.client = Daytona()\n    def run(self, other):\n        del self\n        return self.client.create()",
        "class A:\n    def __init__(self): self.client = Daytona()\n    def run(self):\n        def inner(self): return self.client.create()",
        "class A:\n    def run(self):\n        self.client: Daytona = Other()\n        return self.client.create()",
    ],
)
def test_receiver_stays_unproven_when_a_write_can_replace_it(body: str) -> None:
    # Given a prior client-looking binding which is not guaranteed at the use.
    source = "from daytona import Daytona\n" + body + "\n"
    # When migrating the source with optional receiver writes.
    result = migrate(source)
    # Then no ambiguous receiver is redirected, even if its spelling gets a FIXME.
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "write",
    [
        "self.client = Other()",
        "self.client: Daytona = Other()",
        "self.client = unknown",
        "if condition:\n            self.client = Other()",
        "del self.client",
        "self.client += unknown",
        "for self.client in unknown:\n            pass",
        "with unknown as self.client:\n            pass",
    ],
)
def test_class_attribute_stays_unproven_when_any_write_contradicts_it(
    reverse: bool, write: str
) -> None:
    # Given two setters, one of which is not a client, in either method order.
    methods = [
        "    def connect(self):\n        self.client = Daytona()\n",
        "    def reset(self):\n        " + write + "\n",
    ]
    if reverse:
        methods.reverse()
    source = (
        "from daytona import Daytona\nclass A:\n    client: Daytona\n"
        + "".join(methods)
        + "    def run(self):\n        return self.client.create()\n"
    )
    # When computing the class-wide proof.
    result = migrate(source)
    # Then an annotation or unused client setter cannot hide a contradictory write.
    assert "self.client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "declaration",
    [
        "client: Daytona = Other()",
        "client = Other()",
        "client: Other",
        "client = Daytona()\n    client += unknown",
        "if condition:\n        client = Other()",
    ],
)
def test_class_declaration_stays_unproven_when_it_contradicts_a_setter(
    declaration: str,
) -> None:
    # Given a class declaration plus a client setter.
    source = f"from daytona import Daytona\nclass A:\n    {declaration}\n    def connect(self): self.client = Daytona()\n    def run(self): return self.client.create()\n"
    # When joining declarations and method writes.
    result = migrate(source)
    # Then every class-level source of the attribute participates in the proof.
    assert "self.client.v0.create()" not in result.source


@pytest.mark.parametrize(
    "prefix,guard",
    [
        ("from typing import TYPE_CHECKING as TC", "TC"),
        ("import typing", "typing.TYPE_CHECKING"),
        ("import typing as t", "t.TYPE_CHECKING"),
    ],
)
def test_forward_annotation_when_type_checking_binding_is_aliased(
    prefix: str, guard: str
) -> None:
    # Given an imported binding rather than a magic suffix spelling.
    source = f'{prefix}\nif {guard}:\n    from daytona import Daytona as D\ndef f(d: "D"):\n    return d.create()\n'
    # When the annotation import is analyzed.
    result = migrate(source)
    # Then the forward annotation proves the client.
    assert result.source == source.replace("d.create()", "d.v0.create()")


def test_forward_annotation_stays_unknown_when_type_checking_is_shadowed() -> None:
    # Given an unrelated flag with the previously special-cased spelling.
    source = 'TYPE_CHECKING = False\nif TYPE_CHECKING:\n    from daytona import Daytona as D\ndef f(d: "D"):\n    return d.create()\n'
    # When the flag does not resolve to typing's binding.
    result = migrate(source)
    # Then its optional import supplies review evidence, never a rewrite proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_literal_collection_when_all_elements_are_clients() -> None:
    # Given a homogeneous literal iterable rather than a mapped list producer.
    source = "from daytona import Daytona\nxs = [d.create() for d in [Daytona()]]\n"
    # When inferring the comprehension target.
    result = migrate(source)
    # Then its client call is migrated.
    assert result.source == source.replace("d.create()", "d.v0.create()")


def test_literal_collection_stays_unproven_when_elements_disagree() -> None:
    # Given a collection containing both clients and unrelated objects.
    source = (
        "from daytona import Daytona\nxs = [d.create() for d in [Daytona(), Other()]]\n"
    )
    # When inferring its iteration variable.
    result = migrate(source)
    # Then a single client element cannot prove every iteration's receiver.
    assert result.source == source


@pytest.mark.parametrize(
    "statement",
    [
        "from .daytona import Daytona, Sandbox",
        "from ..daytona import Daytona, Sandbox",
        "if True: from .daytona import Daytona, Sandbox",
    ],
)
def test_relative_import_when_a_local_module_uses_the_sdk_spelling(
    statement: str,
) -> None:
    # Given an unrelated local module with the SDK's public names.
    source = statement + "\nd = Daytona()\nd.create()\n"
    # When transforming the relative import and its uses.
    result = migrate(source)
    # Then neither the import nor the local client is migrated.
    assert result.source == source
    assert not result.found


def test_package_reexport_when_indexing_uses_the_real_initializer_path() -> None:
    # Given a package's relative re-export of a client and annotated factory.
    mapping = load_mapping()
    sources = {
        "pkg": "from .base import client, factory\n",
        "pkg.base": "from daytona import Daytona\nclient = Daytona()\ndef factory() -> Daytona: return Daytona()\n",
        "app": "from pkg import client, factory\nclient.create()\nfactory().create()\n",
    }
    files = {"pkg": "pkg/__init__.py", "pkg.base": "pkg/base.py", "app": "app.py"}
    # When indexing and transforming with the same file identity.
    index = build_index(sources, mapping, files)
    result = transform_source(
        sources["app"], TransformOptions(mapping), SourceContext("app.py", "app", index)
    )
    # Then the relative bridge carries both proofs to its consumer.
    assert result.source == sources["app"].replace(".create()", ".v0.create()")


def test_relative_module_when_imported_through_a_package_namespace() -> None:
    # Given a local daytona module imported as a module rather than public names.
    mapping = load_mapping()
    source = "from . import daytona as sdk\nd = sdk.Daytona()\nd.create()\nvalue = sdk.Sandbox\n"
    sources = {
        "pkg.app": source,
        "pkg.daytona": "class Daytona: pass\nclass Sandbox: pass\n",
    }
    # When the project index can resolve the local module.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("pkg/app.py", "pkg.app", build_index(sources, mapping)),
    )
    # Then a relative module binding is never reinterpreted as the external SDK.
    assert result.source == source


def test_mock_target_when_mock_module_is_imported_under_an_alias() -> None:
    # Given a module import binding rather than a textual mock.patch callee.
    source = "from unittest import mock as doubles\nimport daytona\ndoubles.patch('daytona.Sandbox.delete')\n"
    # When rewriting a resource test-double target.
    result = migrate(source)
    # Then the aliased patch receives the moved namespace.
    assert result.source == source.replace(
        "daytona.Sandbox.delete", "daytona.v0.Sandbox.delete"
    )


def test_local_relative_import_when_real_daytona_dependency_is_present(
    tmp_path: Path,
) -> None:
    # Given an executable local module alongside an external dependency.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/daytona.py").write_text(
        "class Daytona:\n    def create(self): return 'local'\nclass Sandbox: pass\n"
    )
    path = tmp_path / "pkg/app.py"
    source = "from .daytona import Daytona, Sandbox\nprint(Daytona().create())\n"
    path.write_text(source)
    # When upgrading through the CLI with proof indexing enabled.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then the local import remains usable and unchanged.
    assert status == 0
    assert path.read_text() == source
