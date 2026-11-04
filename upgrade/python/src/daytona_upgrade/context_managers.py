# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Binding-based identities for a bounded set of non-suppressing contexts."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Final

from .subclasses import member_receiver

_FACTORIES: Final = frozenset(
    {
        "builtins.open",
        "io.open",
        "threading.Lock",
        "threading.RLock",
        "threading.Semaphore",
        "threading.BoundedSemaphore",
        "tempfile.TemporaryDirectory",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryFile",
        "tempfile.SpooledTemporaryFile",
        "path.open",
    }
)
_PATHS: Final = frozenset({"pathlib.Path", "pathlib.PosixPath", "pathlib.WindowsPath"})


def context_call(function: str, node: ast.Call, infer: Callable[[ast.AST], str]) -> str:
    """Recognize concrete stdlib results, never arbitrary user-defined subclasses."""
    path = function.removeprefix("module:").removeprefix("call:")
    if path in {"contextlib.ExitStack", "contextlib.AsyncExitStack"}:
        return "async-exit-stack" if path.endswith("AsyncExitStack") else "exit-stack"
    if path in {
        "contextlib.ExitStack.enter_context",
        "contextlib.AsyncExitStack.enter_context",
        "contextlib.AsyncExitStack.enter_async_context",
    }:
        asynchronous = path.endswith("enter_async_context")
        if len(node.args) == 1 and not node.keywords:
            return context_entry(infer(node.args[0]), asynchronous)
        if not node.args and len(node.keywords) == 1 and node.keywords[0].arg == "cm":
            return context_entry(infer(node.keywords[0].value), asynchronous)
        return ""
    if path == "contextlib.nullcontext":
        value = (
            node.args[0]
            if len(node.args) == 1
            else next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "enter_result"
                ),
                None,
            )
        )
        return "context-value:" + (infer(value) if value is not None else "")
    if path in _FACTORIES:
        return "context:non-suppressing"
    # Condition delegates __exit__ to its lock, which may be a user suppressor.
    if path == "threading.Condition" and not node.args and not node.keywords:
        return "context:non-suppressing"
    return "path-instance" if path in _PATHS else ""


def context_attribute(parent: str, name: str) -> str:
    """Require proven instances before trusting stdlib instance method contracts."""
    if parent == "async-exit-stack" and name == "enter_async_context":
        return "call:contextlib.AsyncExitStack.enter_async_context"
    if parent in {"exit-stack", "async-exit-stack"} and name == "enter_context":
        return "call:contextlib.ExitStack.enter_context"
    return "call:path.open" if parent == "path-instance" and name == "open" else ""


def context_entry(kind: str, asynchronous: bool) -> str:
    """An overridden entry protocol does not necessarily return its manager."""
    if kind.startswith("context-value:"):
        return kind.removeprefix("context-value:")
    if kind.startswith(("client-subclass:", "self-subclass:")):
        entry = "__aenter__" if asynchronous else "__enter__"
        if member_receiver(kind, entry) != "client":
            return "unproven:client"
    return kind
