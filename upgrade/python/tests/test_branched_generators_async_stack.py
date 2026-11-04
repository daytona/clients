# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Strict yield joins and binding-proven asynchronous context entry results."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize("async_", [False, True])
@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n        yield Daytona()\n    else:\n        yield unknown()",
        "if flag:\n        yield unknown()\n    else:\n        yield Daytona()",
        "yield Daytona()\n    if flag:\n        yield 'foreign'",
        "if flag:\n        value = Daytona()\n    else:\n        value = unknown()\n    yield value",
        "if flag:\n        yield Daytona()\n    else:\n        yield None",
    ],
)
def test_reports_element_when_generator_branches_mix_yield_proofs(
    async_: bool, body: str
) -> None:
    # Given a generator with a proven client and an unresolved or foreign yield.
    prefix = "async " if async_ else ""
    source = (
        f"from daytona import Daytona\n{prefix}def values():\n    {body}\n"
        f"{prefix}def run():\n    {prefix}for value in values():\n"
        "        value.create()\n"
    )
    # When migrating the consumer without selecting a runtime branch.
    result = migrate(source)
    # Then uncertainty is actionable but cannot justify a compatibility rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n        yield Daytona()\n    else:\n        yield Daytona()",
        "if flag:\n        yield from [Daytona()]\n    else:\n        yield Daytona()",
        "if flag:\n        value = Daytona()\n    else:\n        value = Daytona()\n    yield value",
    ],
)
def test_routes_element_when_every_generator_branch_proves_client(body: str) -> None:
    # Given branches whose yielded elements have the same exact client proof.
    source = (
        f"from daytona import Daytona\ndef values():\n    {body}\n"
        "for value in values():\n    value.create()\n"
    )
    # When joining all possible yields.
    result = migrate(source)
    # Then exact homogeneous proof survives the join.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("module", ["typing", "typing_extensions", "collections.abc"])
@pytest.mark.parametrize("position", ["parameter", "return"])
def test_routes_element_when_async_generator_annotation_proves_client(
    module: str, position: str
) -> None:
    # Given a known AsyncGenerator constructor reached through an import alias.
    declaration = (
        "async def run(values: Results[Daytona, None]):\n"
        if position == "parameter"
        else "def values() -> Results[Daytona, None]: return unknown()\n"
        "async def run():\n"
    )
    iterable = "values" if position == "parameter" else "values()"
    source = (
        f"from {module} import AsyncGenerator as Results\n"
        f"from daytona import Daytona\n{declaration}"
        f"    async for value in {iterable}:\n        value.create()\n"
    )
    # When migrating an asynchronous generator element.
    result = migrate(source)
    # Then only its first type argument supplies yielded-element proof.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("manager", ["d", "ctx.nullcontext(d)"])
@pytest.mark.parametrize("stack", ["stack", "ctx.AsyncExitStack()"])
def test_routes_result_when_async_exit_stack_enters_proven_context(
    manager: str, stack: str
) -> None:
    # Given a known stack and a Daytona-typed context or identity wrapper.
    source = (
        "import contextlib as ctx\nfrom daytona import Daytona\n"
        "async def run(d: Daytona):\n    stack = ctx.AsyncExitStack()\n"
        f"    value = await {stack}.enter_async_context({manager})\n"
        "    value.create()\n"
    )
    # When migrating the result of the asynchronous entry protocol.
    result = migrate(source)
    # Then the entry result, rather than the stack itself, supplies receiver proof.
    assert result.source == source.replace("value.create()", "value.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize(
    "setup,expression",
    [
        ("stack = unknown()", "await stack.enter_async_context(d)"),
        (
            "stack = ctx.AsyncExitStack()\n    stack.enter_async_context = unknown",
            "await stack.enter_async_context(d)",
        ),
        (
            "ctx.AsyncExitStack = unknown\n    stack = ctx.AsyncExitStack()",
            "await stack.enter_async_context(d)",
        ),
        (
            "stack = ctx.AsyncExitStack()",
            "await stack.enter_async_context(ctx.nullcontext('foreign'))",
        ),
        ("stack = ctx.AsyncExitStack()", "await stack.enter_async_context(unknown())"),
    ],
)
def test_preserves_result_when_async_entry_lacks_client_proof(
    setup: str, expression: str
) -> None:
    # Given a shadowed stack, an unknown context, or a foreign entry result.
    source = (
        "import contextlib as ctx\nfrom daytona import Daytona\n"
        f"async def run(d: Daytona):\n    {setup}\n    value = {expression}\n"
        "    value.create()\n"
    )
    # When inspecting the stack call without assuming its spelling is identity.
    result = migrate(source)
    # Then unrelated or unknown entry results acquire no Daytona proof.
    assert result.source == source
    assert result.items == ()


def test_reports_result_when_async_stack_enters_overridden_client_protocol() -> None:
    # Given a Daytona subclass whose asynchronous entry returns a foreign object.
    source = (
        "from contextlib import AsyncExitStack\nfrom daytona import Daytona\n"
        "class Sub(Daytona):\n    async def __aenter__(self): return unknown()\n"
        "async def run():\n    stack = AsyncExitStack()\n"
        "    value = await stack.enter_async_context(Sub())\n    value.create()\n"
    )
    # When inspecting the returned entry value rather than the manager itself.
    result = migrate(source)
    # Then a subclass override requires review instead of an unsafe rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n        yield 'foreign'\n    else:\n        yield unknown()",
        "if flag:\n        def nested():\n            yield Daytona()\n    yield 'foreign'",
        "if flag:\n        holder = unknown()\n    else:\n        holder = unknown()\n    yield holder.api",
    ],
)
def test_preserves_element_when_branches_cannot_prove_any_client(body: str) -> None:
    # Given foreign yields, nested-scope yields, or replacement attribute owners.
    source = (
        "from daytona import Daytona\nholder = unknown()\nholder.api = Daytona()\n"
        f"def values():\n    {body}\nfor value in values():\n    value.create()\n"
    )
    # When inferring the generator's own element rather than stale outer evidence.
    result = migrate(source)
    # Then branch handling does not create client evidence from unrelated values.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize("annotation", ["Results[str, Daytona]", "Results[str, None]"])
def test_preserves_element_when_async_generator_yield_annotation_is_foreign(
    annotation: str,
) -> None:
    # Given a client send-type or no client type, but a definitely foreign yield type.
    source = (
        "from typing import AsyncGenerator as Results\nfrom daytona import Daytona\n"
        f"async def run(values: {annotation}):\n"
        "    async for value in values:\n        value.create()\n"
    )
    # When selecting the generator's yielded element annotation.
    result = migrate(source)
    # Then send-value types cannot leak client proof into yielded values.
    assert result.source == source
    assert result.items == ()


def test_preserves_element_when_generator_condition_also_yields_unknown() -> None:
    # Given a suspended branch condition that also yields an unresolved element.
    source = (
        "from daytona import Daytona\ndef values():\n"
        "    if (yield unknown()):\n        yield Daytona()\n"
        "    else:\n        yield Daytona()\n"
        "for value in values():\n    value.create()\n"
    )
    # When considering the branch bodies without executing the condition.
    result = migrate(source)
    # Then ignoring the additional yielded value cannot certify homogeneous clients.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
