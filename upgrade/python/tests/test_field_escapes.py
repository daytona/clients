# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Every call or storage escape fences holder fields, never client identity."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "escape",
    [
        "opaque(holder)",
        "opaque(value=holder)",
        "opaque(*holder)",
        "opaque(**holder)",
        "opaque([holder])",
        "opaque((holder,))",
        "opaque({holder})",
        "opaque({holder: 'key'})",
        "opaque({'key': holder})",
        "opaque([({'nested': {holder}},)])",
        "holder.method()",
        "print(holder)",
        "len(holder)",
        "alias = holder",
        "alias: Holder = holder",
        "(alias := holder)",
        "stored = [holder]",
        "stored = (holder,)",
        "stored = {holder}",
        "stored = {holder: 'key'}",
        "stored = {'key': holder}",
        "stored[0] = holder",
        "stored.owner = holder",
        "def mutate(x): x.client = Other()\ndef wrapper(x): mutate(x)\nwrapper(holder)",
        "def read(): return holder.client\nread()",
        "read = lambda: holder.client\nread()",
        "def read(): return holder.client\nalias = read\nalias()",
        "def read(): return holder.client\ndef wrapper(): return read()\nwrapper()",
        "def read(): return holder.client\nopaque(read)",
    ],
)
def test_fields_require_review_when_holder_escapes(escape: str) -> None:
    # Given two proven fields of the same holder.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\nholder.second = Daytona()\n"
        + escape
        + "\nholder.client.create()\nholder.second.stop()\n"
    )
    # When the holder is exposed to another owner or any call.
    result = migrate(source)
    # Then all its fields require review without executable rewrites.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]


@pytest.mark.parametrize(
    "operation", ["print(other)", "opaque(other)", "alias = other"]
)
def test_proofs_survive_when_only_an_unrelated_object_escapes(operation: str) -> None:
    # Given independent holder fields and a direct client.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\nclient = Daytona()\nother = Other()\n"
        + operation
        + "\nhelper(client)\nholder.client.create()\nclient.create()\n"
    )
    # When only the unrelated object escapes and the direct client is passed.
    result = migrate(source)
    # Then neither client identity nor the independent holder field is lost.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "imported", ["from helpers import mutate", "from helpers import *"]
)
def test_fields_require_review_when_helper_is_imported(imported: str) -> None:
    # Given either explicit or wildcard imports of the same helper.
    sources = {
        "helpers": "def mutate(x): x.client = Other()\n",
        "app": "from daytona import Daytona\n"
        + imported
        + "\nclass Holder: pass\nholder = Holder()\nholder.client = Daytona()\nmutate(holder)\nholder.client.create()\n",
    }
    mapping = load_mapping()
    # When the helper receives the holder.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then import style cannot preserve stale field proofs.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources["app"]))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "hook,call", [("__str__", "print(holder)"), ("__len__", "len(holder)")]
)
def test_fields_require_review_when_builtin_can_run_a_hook(
    hook: str, call: str
) -> None:
    # Given a builtin whose protocol hook can replace the holder's field.
    source = (
        "from daytona import Daytona\nclass Holder:\n"
        f"    def {hook}(self):\n        self.client = Other()\n"
        "holder = Holder()\nholder.client = Daytona()\n"
        + call
        + "\nholder.client.create()\n"
    )
    # When the protocol hook may run.
    result = migrate(source)
    # Then the later field access cannot be rewritten.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
