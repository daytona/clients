# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Call-site records and review-only links from arguments to project parameters."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .client_parameters import ClientArguments
from .review_forwarding import forwarded_arguments

if TYPE_CHECKING:
    from .flow import Flow

CALL_PARAMETERS: Final = ".@call-parameters"
BOUND_CALL_PARAMETERS: Final = ".@bound-call-parameters"


def record_factory_reference(node: ast.expr, flow: Flow) -> None:
    """Escaped callable identities fence automatic adaptation of their results."""
    if isinstance(node, (ast.Name, ast.Attribute)):
        contract = ClientArguments(flow.resolver, flow.env).contract(
            node, CALL_PARAMETERS
        )
        if contract.startswith("fn:"):
            flow.factory_calls[node] = contract.rsplit(":", 1)[0]


@dataclass(frozen=True)
class Call:
    node: ast.Call
    kind: str
    receiver: str
    method: str
    statement: int
    binding: str
    scope: tuple[str, ...]
    # Its parameters provably pass a 0.x Dockerfile image (dockerfile_images).
    dockerfile_image: bool = False


def call_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef, module: str, bound: bool = False
) -> str:
    """Name a bounded, first-party declaration; it supplies no rewrite authority."""
    positional = [*node.args.posonlyargs, *node.args.args]
    slots = [
        f"{arg.arg}={'/' if arg in node.args.posonlyargs else ''}{index - int(bound)}"
        for index, arg in enumerate(positional)
        if not node.decorator_list and arg.annotation is None and index >= int(bound)
    ]
    slots.extend(
        f"{arg.arg}=keyword"
        for arg in node.args.kwonlyargs
        if not node.decorator_list and arg.annotation is None
    )
    return f"fn:{module}:{node.lineno}:" + ";".join(slots)


def record_arguments(node: ast.Call, flow: Flow) -> None:
    """Separate changed consumers from safe adapters; propagate only may-evidence."""
    arguments = ClientArguments(flow.resolver, flow.env)
    flow.client_arguments |= arguments.arguments(node)
    flow.adaptable_arguments |= arguments.arguments(node, safe_only=True)
    flow.review_arguments |= forwarded_arguments(node, arguments)
    flow.client_arguments |= flow.review_arguments
    flow.adaptable_arguments -= flow.review_arguments
    flow.relay_arguments |= arguments.arguments(
        node, safe_only=True, include_relays=True
    )
    contract = arguments.contract(node.func, CALL_PARAMETERS)
    if not contract.startswith("fn:"):
        return
    flow.factory_calls[node] = contract.rsplit(":", 1)[0]
    _, module, line, slots = contract.split(":", 3)
    if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
        kw.arg is None for kw in node.keywords
    ):
        return
    for entry in filter(None, slots.split(";")):
        name, _, slot = entry.partition("=")
        supplied = [
            kw.value
            for kw in node.keywords
            if not slot.startswith("/") and kw.arg == name
        ]
        slot = slot.removeprefix("/")
        if slot.isdecimal() and int(slot) < len(node.args):
            supplied.append(node.args[int(slot)])
        if len(supplied) == 1 and (
            flow.resolver.infer(supplied[0], flow.env)
            in {"client", "unproven:client", "mock:client", "mock:async-client"}
            or flow.resolver.infer(supplied[0], flow.env).startswith("client-subclass:")
        ):
            flow.parameter_links.add((module, int(line), name))
