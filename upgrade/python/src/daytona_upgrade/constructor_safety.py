# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Recognize project initializers with provable config ownership."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .flow import Flow


def constructs_config(node: ast.FunctionDef, enclosing: Flow) -> bool:
    """Prove a straight-line initializer builds its config instead of receiving one."""
    # AST dispatch uses isinstance because the tool also runs on Python 3.9.
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if (
        node.decorator_list
        or node.args.vararg
        or node.args.kwarg
        or not parameters
        or any(
            parameter.arg in {"config", "super"}
            or enclosing.resolver.annotation(
                parameter.annotation, enclosing.annotation_env()
            )
            == "config"
            for parameter in parameters[1:]
        )
        or not body
        or not isinstance(body[-1], (ast.Expr, ast.Return))
    ):
        return False
    call = body[-1].value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "__init__"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
        and not call.func.value.args
        and not call.func.value.keywords
        and len(call.args) + len(call.keywords) == 1
        and all(keyword.arg == "config" for keyword in call.keywords)
    ):
        return False
    config = call.args[0] if call.args else call.keywords[0].value
    if len(body) == 2 and isinstance(body[0], (ast.Assign, ast.AnnAssign)):
        assignment = body[0]
        targets = (
            assignment.targets
            if isinstance(assignment, ast.Assign)
            else [assignment.target]
        )
        if not (
            len(targets) == 1
            and isinstance(targets[0], ast.Name)
            and isinstance(config, ast.Name)
            and config.id == targets[0].id
            and config.id not in {parameter.arg for parameter in parameters}
            and assignment.value is not None
        ):
            return False
        config = assignment.value
    elif len(body) != 1:
        return False
    return (
        isinstance(config, ast.Call)
        and enclosing.resolver.infer(
            config.func,
            {
                name: kind
                for name, kind in enclosing.env.items()
                if name not in {parameter.arg for parameter in parameters}
            },
        )
        == "type:config"
        and not config.args
        and all(
            keyword.arg is not None
            and isinstance(keyword.value, (ast.Name, ast.Constant))
            for keyword in config.keywords
        )
    )


def forwards_parameters(node: ast.FunctionDef) -> bool:
    """Accept only an undecorated, single-call super initializer."""
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    if node.decorator_list or len(body) != 1:
        return False
    statement = body[0]
    # AST dispatch uses isinstance because the tool also runs on Python 3.9.
    if not isinstance(statement, (ast.Expr, ast.Return)):
        return False
    call = statement.value
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "__init__"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
        and not call.func.value.args
        and not call.func.value.keywords
    ):
        return False
    signature = node.args
    positional = [*signature.posonlyargs, *signature.args]
    if not positional:
        return False
    parameters = [*positional, *signature.kwonlyargs]
    parameters.extend(arg for arg in [signature.vararg, signature.kwarg] if arg)
    if any(arg.arg == "super" for arg in parameters):
        return False
    expected = [arg.arg for arg in positional[1:]]
    remaining = {arg.arg for arg in signature.kwonlyargs}
    index = 0
    starred = False
    for argument in call.args:
        if isinstance(argument, ast.Starred):
            if (
                starred
                or index != len(expected)
                or signature.vararg is None
                or not isinstance(argument.value, ast.Name)
                or argument.value.id != signature.vararg.arg
            ):
                return False
            starred = True
        elif (
            starred
            or index >= len(expected)
            or not isinstance(argument, ast.Name)
            or argument.id != expected[index]
        ):
            return False
        else:
            index += 1
    if bool(signature.vararg) != starred:
        return False
    # Positional-only parameters cannot be forwarded as keywords.
    if index < len(signature.posonlyargs) - 1:
        return False
    remaining.update(expected[index:])
    expanded = False
    for keyword in call.keywords:
        if keyword.arg is None:
            if (
                expanded
                or signature.kwarg is None
                or not isinstance(keyword.value, ast.Name)
                or keyword.value.id != signature.kwarg.arg
            ):
                return False
            expanded = True
        elif (
            keyword.arg not in remaining
            or not isinstance(keyword.value, ast.Name)
            or keyword.value.id != keyword.arg
        ):
            return False
        else:
            remaining.remove(keyword.arg)
    return not remaining and bool(signature.kwarg) == expanded
