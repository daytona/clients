# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Call escapes invalidate holder fields, never standalone client bindings."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "write",
    [
        "x.client = Other()",
        "alias = x\n    alias.client = Other()",
        "del x.client",
        "setattr(x, 'client', Other())",
        "object.__setattr__(x, 'client', Other())",
        "x.__dict__['client'] = Other()",
        "del x.__dict__['client']",
        "forward(x)",
    ],
)
@pytest.mark.parametrize(
    "call",
    [
        "change(holder)",
        "change(x=holder)",
        "change(*(holder,))",
        "change(**{'x': holder})",
    ],
)
def test_field_requires_review_when_argument_can_be_mutated(
    write: str, call: str
) -> None:
    # Given a proven field and a callee able to replace it through its parameter.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\ndef change(x):\n    "
        + write
        + "\n"
        + call
        + "\nholder.client.create()\n"
    )
    # When migrating the subsequent field access.
    result = migrate(source)
    # Then no obsolete field proof survives the call.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "setup,call",
    [
        ("from external import change", "change(holder)"),
        ("", "holder.mutate()"),
        ("def change():\n    holder.client = Other()", "change()"),
        ("change = lambda: setattr(holder, 'client', Other())", "change()"),
        ("alias = holder\nfrom external import change", "change(alias)"),
    ],
)
def test_field_requires_review_when_holder_escapes(setup: str, call: str) -> None:
    # Given a holder exposed to opaque code, including a called closure.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\n"
        + setup
        + "\n"
        + call
        + "\nholder.client.create()\n"
    )
    # When the call may change the stored client.
    result = migrate(source)
    # Then the original access remains available for manual review.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "setup,call",
    [
        ("", "len(holder)"),
        ("", "print(holder)"),
        ("", "repr(holder)"),
        ("", "str(holder)"),
        ("", "isinstance(holder, Holder)"),
        ("", "id(holder)"),
        ("", "type(holder)"),
        ("import logging", "logging.info('%s', holder)"),
        (
            "import logging\nlogger = logging.getLogger(__name__)",
            "logger.info('%s', holder)",
        ),
        ("def inspect(x):\n    return x.client", "inspect(holder)"),
        (
            "def inspect(x):\n    alias = x\n    return alias.client",
            "inspect(x=holder)",
        ),
        ("def inspect():\n    return holder.client", "inspect()"),
    ],
)
def test_field_requires_review_when_call_appears_read_only(
    setup: str, call: str
) -> None:
    # Given an apparently read-only call receiving or capturing the holder.
    source = (
        "from daytona import Daytona\nclass Holder: pass\nholder = Holder()\n"
        "holder.client = Daytona()\n"
        + setup
        + "\n"
        + call
        + "\nholder.client.create()\n"
    )
    # When applying the conservative call boundary.
    result = migrate(source)
    # Then apparent read-only behavior never preserves a field proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


def test_client_moves_when_opaque_helper_receives_client_itself() -> None:
    # Given a standalone client binding, not a field of the argument.
    source = "from daytona import Daytona\nfrom external import helper\nclient = Daytona()\nhelper(client)\nclient.create()\n"
    # When the helper may mutate the object's attributes but cannot rebind client.
    result = migrate(source)
    # Then the client's identity proof remains valid.
    assert result.source == source.replace("client.create()", "client.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("body", ["x.client = Other()", "return x.client"])
def test_imported_call_requires_review_when_helper_is_in_another_module(
    body: str,
) -> None:
    # Given a project helper with either a write or only a read.
    sources = {
        "helpers": "def inspect(x):\n    " + body + "\n",
        "app": "from daytona import Daytona\nfrom helpers import inspect\nclass Holder: pass\nholder = Holder()\nholder.client = Daytona()\ninspect(holder)\nholder.client.create()\n",
    }
    mapping = load_mapping()
    # When migration follows the project's helper export.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then neither a read nor a write permits keeping the field proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources["app"]))
    assert any(item["kind"] == "fixme" for item in result.items)
