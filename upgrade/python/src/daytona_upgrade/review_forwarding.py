# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Review-only argument links for framework field injection and async runners."""

from __future__ import annotations

import ast

from .client_parameters import ClientArguments
from .field_effects import CLIENT_ANNOTATION


def forwarded_arguments(node: ast.Call, arguments: ClientArguments) -> set[ast.expr]:
    """These adapters identify changed consumers but never own the supplied fake."""
    function = arguments.resolver.infer(node.func, arguments.env)
    if function in {"module:anyio.run", "module:trio.run"} and node.args:
        return arguments.arguments(
            ast.Call(func=node.args[0], args=node.args[1:], keywords=[])
        )
    if (
        function == "module:asyncio.run"
        and node.args
        and isinstance(node.args[0], ast.Call)
    ):
        return arguments.arguments(node.args[0])
    receiver: ast.expr | None = None
    values = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
    if function in {
        "module:attrs.evolve",
        "module:attr.evolve",
        "module:dataclasses.replace",
    }:
        keyword = "obj" if function == "module:dataclasses.replace" else "inst"
        receiver = node.args[0] if node.args else values.get(keyword)
    elif isinstance(node.func, ast.Attribute):
        if node.func.attr == "model_construct":
            receiver = node.func.value
        elif node.func.attr == "model_copy":
            receiver = node.func.value
            update = values.get("update")
            values = (
                {
                    key.value: value
                    for key, value in zip(update.keys, update.values)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                if isinstance(update, ast.Dict)
                else {}
            )
    if receiver is None:
        return set()
    kind = arguments.resolver.infer(receiver, arguments.env)
    if not kind.startswith(("class:", "instance:")) or kind.endswith(":review"):
        return set()
    owner = kind.split(":")[1]
    return {
        value
        for name, value in values.items()
        if arguments.env.get(owner + "." + name) == "client"
        or arguments.env.get(owner + ".@factory." + name) == "client"
        or arguments.env.get(owner + CLIENT_ANNOTATION + name) == "client"
    }
