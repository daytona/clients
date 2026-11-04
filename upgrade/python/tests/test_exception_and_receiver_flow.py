# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Control-flow joins preserve conservative receiver identity."""

from __future__ import annotations

import sys

import pytest
from test_flow import migrate


@pytest.mark.skipif(sys.version_info < (3, 11), reason="except* requires Python 3.11")
def test_receiver_stays_unproven_when_exception_group_handler_is_optional() -> None:
    # Given a handler which does not execute on the normal path.
    source = "from daytona import Daytona\nd = Other()\ntry:\n    work()\nexcept* ValueError:\n    d = Daytona()\nd.create()\n"
    # When joining the exception-group paths.
    result = migrate(source)
    # Then the handler cannot prove the normal path's unrelated receiver.
    assert "d.v0.create()" not in result.source


@pytest.mark.parametrize("exit_statement", ["return", "raise Error()"])
def test_finally_receiver_stays_unproven_when_client_assignment_is_dead(
    exit_statement: str,
) -> None:
    # Given a dead store after an abrupt exit into finally.
    source = f"from daytona import Daytona\ndef run():\n    d = Other()\n    try:\n        {exit_statement}\n        d = Daytona()\n    finally:\n        d.create()\n"
    # When analyzing the finalizer's entry state.
    result = migrate(source)
    # Then the dead assignment never establishes a client proof.
    assert "d.v0.create()" not in result.source


def test_finally_receiver_when_handler_prefix_may_bind_client() -> None:
    # Given a handler that binds a client before a later exception unwinds.
    source = "from daytona import Daytona\ntry:\n    fail()\nexcept Error:\n    d = Daytona()\n    fail_again()\nfinally:\n    d.create()\n"
    # When analyzing every handler prefix that can enter finally.
    result = migrate(source)
    # Then client evidence is retained as a FIXME rather than discarded.
    assert "FIXME(daytona-upgrade)" in result.source
    assert "d.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_finally_receiver_when_else_prefix_may_bind_client() -> None:
    # Given an else-suite assignment followed by an exception into finally.
    source = "from daytona import Daytona\ntry:\n    work()\nexcept Error:\n    pass\nelse:\n    d = Daytona()\n    fail_again()\nfinally:\n    d.create()\n"
    # When analyzing every else prefix that can enter finally.
    result = migrate(source)
    # Then the differing entry evidence remains a FIXME.
    assert "FIXME(daytona-upgrade)" in result.source
    assert "d.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_finally_receiver_when_all_prefixes_bind_non_client() -> None:
    # Given all try, handler, and else prefixes bound to an httpx client.
    source = "import httpx\nd = httpx.Client()\ntry:\n    work()\nexcept Error:\n    d = httpx.Client()\nelse:\n    d = httpx.Client()\nfinally:\n    d.create()\n"
    # When joining the states that can enter finally.
    result = migrate(source)
    # Then non-client evidence does not create a manual migration item.
    assert result.items == ()
    assert "d.v0.create()" not in result.source


def test_receiver_stays_unproven_when_break_skips_while_else() -> None:
    # Given a break path which skips the only client-producing assignment.
    source = "from daytona import Daytona\nd = Other()\nwhile condition:\n    break\nelse:\n    d = Daytona()\nd.create()\n"
    # When joining the loop's possible exits.
    result = migrate(source)
    # Then the skipped else cannot redirect the unrelated object's call.
    assert "d.v0.create()" not in result.source


@pytest.mark.parametrize("guard", ["TYPE_CHECKING", "typing.TYPE_CHECKING"])
def test_constructor_stays_unproven_when_type_only_import_conflicts_with_runtime(
    guard: str,
) -> None:
    # Given different constructor bindings for typing and execution.
    source = f"from typing import TYPE_CHECKING\nimport typing\nif {guard}:\n    from daytona import Daytona\nelse:\n    from legacy import Daytona\nd = Daytona()\nd.create()\n"
    # When resolving the runtime constructor.
    result = migrate(source)
    # Then the type-only SDK import cannot prove the legacy object.
    assert "d.v0.create()" not in result.source


@pytest.mark.parametrize(
    "body",
    [
        "api = Daytona()\ndef run():\n    return api.create()\napi = Other()",
        "def outer():\n    api = Daytona()\n    def run():\n        return api.create()\n    api = Other()\n    return run",
        "api = Daytona()\nrun = lambda: api.create()\napi = Other()",
        "from daytona import Daytona as D\ndef run():\n    return D().create()\nD = Other",
        "api = Daytona()\ndef run():\n    return api.create()\ndef reset():\n    global api\n    api = Other()",
        "def outer():\n    api = Daytona()\n    def run():\n        return api.create()\n    def reset():\n        nonlocal api\n        api = Other()\n    return run, reset",
        "api = Daytona(); run = lambda: api.create(); api = Other()",
        "api = Daytona()\napi = lambda: api.create()",
        "api = Daytona()\napi: Callable = lambda: api.create()",
        "api = Daytona()\n(api := lambda: api.create())",
        "class Worker:\n    def connect(self): self.client = Daytona()\n    def run(self): return self.client.create()\nDaytona = Other",
        "def outer():\n    D = Daytona\n    class Worker:\n        def connect(self): self.client = D()\n        def run(self): return self.client.create()\n    D = Other\n    return Worker",
    ],
)
def test_deferred_receiver_stays_unproven_when_enclosing_binding_changes(
    body: str,
) -> None:
    # Given Python's late lookup of a global or closure cell.
    source = "from daytona import Daytona\n" + body + "\n"
    # When analyzing a deferred function body rather than executing its definition.
    result = migrate(source)
    # Then a definition-time binding cannot prove the later call's receiver.
    assert ".v0.create()" not in result.source


@pytest.mark.parametrize("replacement", ["other", "Worker()"])
def test_instance_attribute_stays_unproven_when_setter_rebinds_self(
    replacement: str,
) -> None:
    # Given a setter that writes to another object rather than this instance.
    source = f"from daytona import Daytona\nclass Worker:\n    def connect(self, other):\n        self = {replacement}\n        self.client = Daytona()\n    def run(self):\n        return self.client.create()\n"
    # When collecting class-wide attribute proofs.
    result = migrate(source)
    # Then another object's client cannot prove this instance's attribute.
    assert "self.client.v0.create()" not in result.source


@pytest.mark.parametrize("decorator", ["staticmethod", "classmethod"])
def test_instance_attribute_stays_unproven_when_only_setter_is_not_instance_method(
    decorator: str,
) -> None:
    # Given a write through a static/class method's self-spelled argument.
    source = f"from daytona import Daytona\nclass Worker:\n    @{decorator}\n    def connect(self):\n        self.client = Daytona()\n    def run(self):\n        return self.client.create()\n"
    # When collecting instance initialization facts.
    result = migrate(source)
    # Then the class-wide instance proof excludes this non-instance write.
    assert "self.client.v0.create()" not in result.source


@pytest.mark.parametrize(
    "method",
    [
        "@staticmethod\n    def run(self):",
        "@classmethod\n    def run(self):",
        "def run(other, self):",
        "@staticmethod\n    def run():",
        "def run(other):",
    ],
)
def test_instance_attribute_stays_unproven_when_parameter_is_not_receiver(
    method: str,
) -> None:
    # Given a self-spelled parameter which is not the implicit instance receiver.
    source = f"from daytona import Daytona\nclass Worker:\n    def __init__(self): self.client = Daytona()\n    {method}\n        return self.client.create()\n"
    # When entering the method's actual argument scope.
    result = migrate(source)
    # Then class-wide facts are not transferred to a caller-provided object.
    assert "self.client.v0.create()" not in result.source


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match requires Python 3.10")
def test_deferred_receiver_stays_unproven_when_match_rebinds_global() -> None:
    # Given a later pattern capture which writes the global used by a function.
    source = "from daytona import Daytona\napi = Daytona()\ndef run():\n    return api.create()\nmatch Other():\n    case api: pass\n"
    # When accounting for future lexical writes.
    result = migrate(source)
    # Then a pattern capture invalidates the inherited definition-time proof.
    assert "api.v0.create()" not in result.source


@pytest.mark.parametrize(
    "body,call",
    [
        ("api = Daytona()\ndef run():\n    return api.create()", "api.create()"),
        ("api = Daytona()\nrun = lambda: api.create()", "api.create()"),
        (
            "def outer():\n    api = Daytona()\n    def run():\n        return api.create()\n    return run",
            "api.create()",
        ),
        (
            "class Worker:\n    def __init__(self): self.client = Daytona()\n    def run(self, /): return self.client.create()",
            "self.client.create()",
        ),
        (
            "d = Other()\nwhile condition:\n    pass\nelse:\n    d = Daytona()\nd.create()",
            "d.create()",
        ),
        (
            "def run():\n    try:\n        return\n    finally:\n        d = Daytona()\n        d.create()",
            "d.create()",
        ),
    ],
)
def test_client_call_is_migrated_when_scope_or_exit_proof_is_stable(
    body: str,
    call: str,
) -> None:
    # Given a stable inherited binding, actual receiver, or live finalizer assignment.
    source = "from daytona import Daytona\n" + body + "\n"
    # When applying the same conservative boundary rules.
    result = migrate(source)
    # Then valid proofs still produce the required executable namespace rewrite.
    assert result.source == source.replace(
        call, call.replace(".create()", ".v0.create()")
    )
