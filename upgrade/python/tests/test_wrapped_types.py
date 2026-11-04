# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Wrapped producers and type operands retain migration provenance."""

from __future__ import annotations

import sys

import pytest
from test_flow import migrate
from test_workflow_binding_lifetimes import analyzed

from daytona_upgrade.persistent_workflow import persistent_lifecycle_calls
from daytona_upgrade.suggestion_owners import ServiceOwners
from daytona_upgrade.suggestions import workflow_methods


def test_skips_persistence_when_method_is_absent_from_v0() -> None:
    # Given a workflow with a method that does not exist in the 0.x SDK.
    flow = analyzed(
        "from daytona import Daytona\nclient = Daytona()\n"
        "sandbox = client.v0.create()\nsandbox.resume()\n"
    )
    # When classifying persistence requirements.
    result = persistent_lifecycle_calls(flow.calls[0], flow, ServiceOwners(flow))
    # Then the unsupported method does not fence migration.
    assert result == []


@pytest.mark.parametrize(
    "producer",
    ["cast(object, client.v0.create())", "(client.v0.create())"],
)
@pytest.mark.parametrize("annotated", [True, False])
def test_keeps_calls_when_producer_has_trailing_wrapper(
    producer: str, annotated: bool
) -> None:
    # Given a wrapped producer, followed by calls on both sides of a rebinding.
    target = "sandbox: object" if annotated else "sandbox"
    source = (
        "from daytona import Daytona\nfrom typing import cast\nclient = Daytona()\n"
        f"{target} = {producer}\n"
        "client.v0.delete(sandbox)\nclient.v0.stop(sandbox)\n"
        "sandbox = client.v0.create()\nclient.v0.start(sandbox)\n"
    )
    flow = analyzed(source)
    producer_call = next(call for call in flow.calls if call.method == "create")
    # When collecting the first workflow's calls and persistence requirements.
    methods = workflow_methods(producer_call, flow)
    persistent = persistent_lifecycle_calls(producer_call, flow, ServiceOwners(flow))
    # Then the producer store is skipped, but the later reassignment still fences.
    assert methods == ["client.v0.delete", "client.v0.stop"]
    assert persistent == ["client.v0.stop"]


@pytest.mark.parametrize(
    "statement",
    [
        'x = ForwardRef("daytona.Sandbox")',
        'x = typing.ForwardRef("daytona.Sandbox")',
        'x = Ref("daytona.Sandbox")',
        'x: list[ForwardRef("daytona.Sandbox")]',
        "x: \"list[ForwardRef('daytona.Sandbox')]\"",
        pytest.param(
            'x: tuple[*tuple["daytona.Sandbox"]]',
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11),
                reason="PEP 646 star syntax needs Python 3.11",
            ),
        ),
        pytest.param(
            "x: \"tuple[*tuple['daytona.Sandbox']]\"",
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11),
                reason="PEP 646 star syntax needs Python 3.11",
            ),
        ),
        'x: Unpack[tuple["daytona.Sandbox"]]',
        pytest.param(
            'x: tuple[*Unpack[tuple["daytona.Sandbox"]]]',
            marks=pytest.mark.skipif(
                sys.version_info < (3, 11),
                reason="PEP 646 star syntax needs Python 3.11",
            ),
        ),
    ],
)
def test_rewrites_refs_when_type_operands_wrap_strings(statement: str) -> None:
    # Given binding-proven ForwardRef calls or unpacked annotation operands.
    source = (
        "import daytona\nimport typing\n"
        "from typing import ForwardRef, ForwardRef as Ref, Unpack\n" + statement + "\n"
    )
    # When upgrading through the real source transformation.
    result = migrate(source)
    # Then only the referenced SDK type path moves.
    assert result.source == source.replace("daytona.Sandbox", "daytona.v0.Sandbox")


@pytest.mark.parametrize(
    "statement",
    [
        'x: tuple[*get_type("daytona.Sandbox")]',
        'x: list[ForwardRef(get_type("daytona.Sandbox"))]',
        'x = ForwardRef("Other", module="daytona.Sandbox")',
        'x: foreign.ForwardRef("daytona.Sandbox")',
        'x: list[ForwardRef(*["daytona.Sandbox"])]',
    ],
)
def test_preserves_values_when_forward_ref_like_calls_are_not_types(
    statement: str,
) -> None:
    # Given runtime values, metadata, or an unrelated ForwardRef implementation.
    source = (
        "import daytona\nimport foreign\n"
        "from typing import ForwardRef, Literal, Annotated\n" + statement + "\n"
    )
    # When upgrading type references.
    result = migrate(source)
    # Then no runtime string is rewritten.
    assert result.source == source
