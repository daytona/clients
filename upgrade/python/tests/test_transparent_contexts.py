# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Only binding-proven non-suppressing context managers retain body proofs."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize("name", ["daytona", "dt"])
@pytest.mark.parametrize(
    "setup,context",
    [
        ("", 'open("key.txt") as f'),
        ("import io", 'io.open("key.txt") as f'),
        ("from io import open as reader", 'reader("key.txt") as f'),
        ("import builtins", 'builtins.open("key.txt") as f'),
        ("import pathlib", 'pathlib.Path("key.txt").open() as f'),
        ("from pathlib import Path", 'Path("key.txt").open() as f'),
        ("from pathlib import Path\np = Path('key.txt')", "p.open() as f"),
        *[
            (f"import threading\nlock = threading.{kind}()", "lock")
            for kind in ["Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition"]
        ],
        *[
            (f"from threading import {kind} as factory", "factory()")
            for kind in ["Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition"]
        ],
        *[
            ("import tempfile", f"tempfile.{kind}()")
            for kind in [
                "TemporaryDirectory",
                "NamedTemporaryFile",
                "TemporaryFile",
                "SpooledTemporaryFile",
            ]
        ],
        ("", "Daytona()"),
    ],
)
def test_proven_when_assignment_completes_in_non_suppressing_context(
    name: str, setup: str, context: str
) -> None:
    # Given a body whose assignment may fail, but whose context cannot suppress it.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"{setup}\nwith {context}:\n"
        f'    {name} = Daytona(DaytonaConfig(api_key="k"))\n{name}.create()\n'
    )
    # When migrating the receiver after successful completion.
    result = migrate(source)
    # Then the established body proof survives without a FIXME.
    assert f"{name}.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize("name", ["daytona", "dt"])
def test_proven_when_assignment_completes_in_async_client_context(name: str) -> None:
    # Given an async-only client context inside an async function.
    source = (
        "from daytona import AsyncDaytona, DaytonaConfig\nasync def run():\n"
        "    async with AsyncDaytona():\n"
        f'        {name} = AsyncDaytona(DaytonaConfig(api_key="k"))\n'
        f"    await {name}.create()\n"
    )
    # When migrating after successful completion of the context.
    result = migrate(source)
    # Then the body proof survives without relying on a sync context protocol.
    assert f"await {name}.v0.create()" in result.source
    assert not result.items


@pytest.mark.parametrize(
    "setup,context",
    [
        ("from contextlib import suppress", "suppress(Exception)"),
        ("", "unknown()"),
        ("open = unknown", "open('key.txt')"),
        ("import io\nio.open = unknown", "io.open('key.txt')"),
        ("from pathlib import Path\nPath = unknown", "Path('key.txt').open()"),
        ("import threading\nlock = threading.Lock()\nlock = unknown()", "lock"),
        ("import threading\nthreading.Lock = unknown", "threading.Lock()"),
        (
            "import tempfile\ntempfile.TemporaryDirectory = unknown",
            "tempfile.TemporaryDirectory()",
        ),
        ("import threading", "threading.Condition(unknown())"),
        ("", "Daytona(), unknown()"),
        ("", "unknown(), Daytona()"),
    ],
)
def test_unproven_when_context_is_unknown_or_shadowed(setup: str, context: str) -> None:
    # Given a possibly suppressing context, including shadowed known spellings.
    source = (
        "from daytona import Daytona\n"
        f"{setup}\nwith {context}:\n    client = Daytona()\nclient.create()\n"
    )
    # When migrating after the block.
    result = migrate(source)
    # Then suppression still prevents an unconditional client proof.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_unproven_when_unknown_outer_context_contains_transparent_inner() -> None:
    # Given a suppressing outer block around an otherwise transparent context.
    source = (
        "from daytona import Daytona\nfrom contextlib import suppress\n"
        "with suppress(Exception):\n    with open('key.txt'):\n"
        "        client = Daytona()\nclient.create()\n"
    )
    # When migrating after both contexts.
    result = migrate(source)
    # Then the outer exceptional path still loses proof.
    assert "client.v0.create()" not in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


def test_proven_when_async_client_context_completes() -> None:
    # Given the async root client itself as a non-suppressing context.
    source = (
        "from daytona import AsyncDaytona\nasync def run():\n"
        "    async with AsyncDaytona() as dt:\n        client = dt\n"
        "    await client.create()\n"
    )
    # When migrating after a normal exit.
    result = migrate(source)
    # Then both the context target and its body alias retain client proof.
    assert "await client.v0.create()" in result.source
    assert not result.items
