# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Observe typed relays at the same lexical call boundary as consumer contracts."""

from __future__ import annotations

import ast

from .call_sites import record_arguments
from .client_parameters import ClientArguments
from .flow import SourceContext
from .resolution import Resolver
from .return_proofs import ReturnFlow


def expanded_call(node: ast.Call) -> ast.Call | None:
    """Literal carriers have exact slots; dynamic unpacking supplies no proof."""
    args: list[ast.expr] = []
    keywords: list[ast.keyword] = []
    for argument in node.args:
        if isinstance(argument, ast.Starred):
            if not isinstance(argument.value, (ast.List, ast.Tuple)) or any(
                isinstance(value, ast.Starred) for value in argument.value.elts
            ):
                return None
            args.extend(argument.value.elts)
        else:
            args.append(argument)
    for keyword in node.keywords:
        if keyword.arg is not None:
            keywords.append(keyword)
        elif isinstance(keyword.value, ast.Dict):
            for key, value in zip(keyword.value.keys, keyword.value.values):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    return None
                keywords.append(ast.keyword(arg=key.value, value=value))
        else:
            return None
    names = [keyword.arg for keyword in keywords]
    if len(set(names)) != len(names):
        return None
    return ast.Call(func=node.func, args=args, keywords=keywords)


class ParameterFlow(ReturnFlow):
    """Collect call-result identity separately from ordinary receiver kinds."""

    def __init__(self, resolver: Resolver, context: SourceContext) -> None:
        super().__init__(resolver, context)
        self.relays: dict[ast.expr, tuple[ast.Call, str]] = {}

    def visit_Call(self, node: ast.Call) -> None:
        if self.previewing_loop:
            super().visit_Call(node)
            return
        arguments = ClientArguments(self.resolver, self.env)
        expanded = expanded_call(node)
        if expanded is None:
            if any(
                not entry.partition("=")[2].startswith("~")
                for entry in filter(None, arguments.contract(node.func).split(";"))
            ):
                # An unresolved carrier can still reach a changed consumer. Keep
                # each input as review evidence without certifying a target slot.
                self.client_arguments.update(
                    child
                    for value in [*node.args, *(kw.value for kw in node.keywords)]
                    for child in ast.walk(value)
                    if isinstance(child, ast.Name)
                )
        else:
            record_arguments(expanded, self)
            for entry in filter(None, arguments.contract(node.func).split(";")):
                name, _, slot = entry.partition("=")
                if not slot.startswith("~"):
                    continue
                path, separator, position = slot.removeprefix("~").partition("/")
                if not separator:
                    path, position = "?", path
                supplied = [kw.value for kw in expanded.keywords if kw.arg == name]
                if position.isdecimal() and int(position) < len(expanded.args):
                    supplied.append(expanded.args[int(position)])
                if len(supplied) == 1:
                    self.relays[supplied[0]] = (node, path)
        super().visit_Call(node)

    def kind(self, node: ast.expr) -> str:
        return self.kinds.get(
            (
                node.lineno,
                node.col_offset,
                node.end_lineno or node.lineno,
                node.end_col_offset or node.col_offset,
            ),
            "",
        )
