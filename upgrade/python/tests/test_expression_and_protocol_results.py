# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Regression coverage for client-bearing expression and protocol results."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "annotation", ["Daytona | Client | None", "Union[Daytona, Client, None]"]
)
def test_mixed_factory_walrus_when_return_can_be_client(annotation: str) -> None:
    # Given a neutral binding assigned from a mixed client factory.
    source = f"""from daytona import Daytona
from httpx import Client
from typing import Union
def next_value() -> {annotation}:
    return Daytona()
while (x := next_value()) is not None:
    x.create()
"""
    # When migrating without a homogeneous receiver proof.
    result = migrate(source)
    # Then manual review is explicit and no namespace is guessed.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("operator", ["and", "or"])
@pytest.mark.parametrize("call_site", ["body", "condition"])
def test_short_circuit_walrus_when_first_operand_assigns_client(
    operator: str, call_site: str
) -> None:
    # Given an unconditional first operand and a neutral walrus target.
    condition = f"(x := d) {operator} " + (
        "x.create()" if call_site == "condition" else "ready()"
    )
    body = "pass" if call_site == "condition" else "x.create()"
    source = (
        f"from daytona import Daytona\nd = Daytona()\nwhile {condition}:\n    {body}\n"
    )
    # When traversing the short-circuit expression.
    result = migrate(source)
    # Then the client call is migrated or explicitly flagged, never silently missed.
    assert ".v0.create()" in result.source or any(
        item["kind"] == "fixme" for item in result.items
    )


@pytest.mark.parametrize("target", ["pool[-1]", "pool[0]"])
def test_augmented_collection_when_loop_carries_client_element(target: str) -> None:
    # Given a list populated on an earlier iteration.
    source = f"""from daytona import Daytona
d = Daytona()
pool = []
for i in [0, 1]:
    if i:
        {target}.create()
    pool += [d]
"""
    # When migrating an element with client evidence but no alias identity.
    result = migrate(source)
    # Then the uncertain receiver is retained for manual review.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("argument", ["cm", "cm=cm"])
def test_exit_stack_when_enter_context_returns_client(argument: str) -> None:
    # Given a concrete stdlib stack and context carrying a client.
    source = f"""from contextlib import ExitStack, nullcontext
from daytona import Daytona
with ExitStack() as stack:
    cm = nullcontext(Daytona())
    x = stack.enter_context({argument})
    x.create()
"""
    # When migrating the returned context entry.
    result = migrate(source)
    # Then the proven client is namespaced.
    assert result.source == source.replace("x.create()", "x.v0.create()")


@pytest.mark.parametrize("returned", ["Daytona()", "self"])
def test_augmented_subclass_when_project_protocol_returns_client(returned: str) -> None:
    # Given a project-defined in-place operator, not a builtin client protocol.
    source = f"""from daytona import Daytona
class Custom(Daytona):
    def __iadd__(self, other):
        return {returned}
x = Custom()
x += 1
x.create()
"""
    # When migrating after the protocol may replace the receiver.
    result = migrate(source)
    # Then uncertainty is explicit rather than a silent miss or false rewrite.
    assert ".v0.create()" in result.source or any(
        item["kind"] == "fixme" for item in result.items
    )
    if returned != "self":
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "body",
    [
        "pool = []\nfor i in [0, 1]:\n    if i:\n        pool[-1].get('/')\n    pool += [Client()]",
        "x = Client()\nwhile (y := x) and ready():\n    y.get('/')",
        "with ExitStack() as stack:\n    x = stack.enter_context(nullcontext(Client()))\n    x.get('/')",
        "class Custom:\n    def __iadd__(self, other):\n        return self\nx = Custom()\nx += 1\nx.get('/')",
    ],
)
def test_nonclient_protocols_when_similar_syntax_has_no_sdk_evidence(body: str) -> None:
    # Given ordinary nonclient code alongside an unrelated SDK import.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\nfrom contextlib import ExitStack, nullcontext\n"
        + body
        + "\n"
    )
    # When migrating the same expression shapes.
    result = migrate(source)
    # Then no speculative warnings or edits appear.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("operator", ["and", "or"])
@pytest.mark.parametrize("assignment", ["x = Daytona()", "x = Client()"])
def test_short_circuit_join_when_later_operand_can_replace_receiver(
    operator: str, assignment: str
) -> None:
    # Given a client/nonclient choice on a conditionally evaluated operand.
    source = f"from daytona import Daytona\nfrom httpx import Client\n{assignment}\nready() {operator} (x := Client())\nx.get('/')\n"
    # When joining the skipped and evaluated paths.
    result = migrate(source)
    # Then uncertain results are never rewritten and nonclients stay quiet.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == (
        ["fixme"] if "Daytona()" in assignment else []
    )


@pytest.mark.parametrize("stack", ["ExitStack", "AsyncExitStack"])
@pytest.mark.parametrize("manager", ["Daytona()", "Custom()"])
def test_sync_stack_entry_when_manager_can_override_entry(
    stack: str, manager: str
) -> None:
    # Given a sync manager, possibly a client subclass with a custom entry.
    source = f"""from contextlib import ExitStack, AsyncExitStack
from daytona import Daytona
class Custom(Daytona):
    def __enter__(self):
        return unknown()
stack = {stack}()
x = stack.enter_context({manager})
x.create()
"""
    # When entering via either concrete stdlib stack.
    result = migrate(source)
    # Then only unchanged client entry establishes an exact proof.
    if manager == "Daytona()":
        assert result.source == source.replace("x.create()", "x.v0.create()")
    else:
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert [item["kind"] for item in result.items] == ["fixme"]


def test_nonclient_operations_when_many_similar_receivers_exist() -> None:
    # Given repeated ordinary HTTP results in the presence of an SDK import.
    source = "from daytona import Daytona\nfrom httpx import Client\n"
    source += "def next_value() -> Client | None:\n    return Client()\n"
    source += "while (x := next_value()) is not None and ready():\n"
    source += "".join(f"    x.get('/{index}')\n" for index in range(25))
    # When migrating without any receiver-side client evidence.
    result = migrate(source)
    # Then the factory fallback does not create a warning storm.
    assert result.source == source
    assert result.items == ()
