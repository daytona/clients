# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Capture generated dataclass parameter values in instance-expression proofs."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from .controlflow import BindingVisitor
    from .resolution import Resolver

ARGUMENTS: Final = ":@arguments:"


def dataclass_parameters(
    node: ast.ClassDef, enclosing: BindingVisitor
) -> dict[str, str]:
    """Retain field order separately from class/default lookup kinds."""
    from .attribute_proofs import _review_only

    parameters: dict[str, str] = {}
    # Inherited fields still accept keywords; base signatures are not positional proof.
    for base in reversed(node.bases):
        kind = enclosing.resolver.infer(base, enclosing.env)
        if not kind.startswith("class:"):
            continue
        owner = kind.split(":")[1]
        prefix = owner + ".@parameter."
        for key, slot in enclosing.env.items():
            if key.startswith(prefix):
                member = key.removeprefix(prefix)
                parameters[node.name + ".@parameter." + member] = (
                    "excluded" if slot == "excluded" else "unknown"
                )
                parameters[node.name + ".@annotation." + member] = enclosing.env.get(
                    owner + ".@annotation." + member, ""
                )
                parameters[node.name + "." + member] = _review_only(
                    enclosing.env.get(
                        owner + ".@factory." + member,
                        enclosing.env.get(owner + "." + member, ""),
                    )
                )
    # Base fields or custom initialization make positional routing uncertain.
    position = (
        -1
        if node.bases
        or any(
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and statement.name in {"__init__", "__new__"}
            for statement in node.body
        )
        else 0
    )
    keyword_only: bool | None = False
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            for kw in decorator.keywords:
                if kw.arg == "kw_only":
                    keyword_only = (
                        kw.value.value
                        if isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, bool)
                        else None
                    )
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(
            statement.target, ast.Name
        ):
            continue
        annotation = statement.annotation
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except SyntaxError:
                # An opaque annotation may hide ClassVar and change every position.
                position = -1
        annotation_kind = enclosing.resolver.infer(
            annotation.value if isinstance(annotation, ast.Subscript) else annotation,
            enclosing.env,
        )
        if annotation_kind == "module:typing.ClassVar":
            continue
        if (
            enclosing.resolver.infer(annotation, enclosing.env)
            == "module:dataclasses.KW_ONLY"
        ):
            keyword_only = True
            continue
        options = (
            {kw.arg: kw.value for kw in statement.value.keywords}
            if isinstance(statement.value, ast.Call)
            and enclosing.resolver.infer(statement.value.func, enclosing.env)
            == "call:dataclasses.field"
            else {}
        )
        init = options.get("init")
        kw_only = options.get("kw_only")
        excluded = isinstance(init, ast.Constant) and init.value is False
        field_keyword_only = (
            keyword_only
            if kw_only is None
            else kw_only.value
            if isinstance(kw_only, ast.Constant) and isinstance(kw_only.value, bool)
            else None
        )
        if not excluded and field_keyword_only is None:
            position = -1
        slot = (
            "excluded"
            if excluded
            else "keyword"
            if field_keyword_only
            else str(position)
            if position >= 0
            else "unknown"
        )
        if None in options or init is not None and not isinstance(init, ast.Constant):
            slot = "unknown"
            position = -1
        if annotation_kind != "module:dataclasses.InitVar":
            parameters[node.name + ".@parameter." + statement.target.id] = slot
            parameters[node.name + ".@annotation." + statement.target.id] = (
                enclosing.resolver.annotation(
                    statement.annotation, enclosing.annotation_env()
                )
            )
        if not excluded and not field_keyword_only and position >= 0:
            position += 1
    return parameters


def instance_kind(node: ast.Call, resolver: Resolver, env: dict[str, str]) -> str:
    """An explicit argument replaces the default even if its value is unknown."""
    function = resolver.infer(node.func, env)
    owner = function.split(":")[1]
    prefix = owner + ".@parameter."
    fields: list[str] = []
    for name, slot in env.items():
        if not name.startswith(prefix):
            continue
        member = name.removeprefix(prefix)
        default = env.get(
            owner + ".@factory." + member, env.get(owner + "." + member, "")
        )
        annotation = env.get(owner + ".@annotation." + member, "")
        supplied = False
        argument: ast.AST | None = None
        if slot != "excluded":
            supplied = (
                any(isinstance(arg, ast.Starred) for arg in node.args)
                or any(kw.arg is None for kw in node.keywords)
                or slot == "unknown"
                and bool(node.args)
            )
            if not supplied:
                argument = next(
                    (kw.value for kw in node.keywords if kw.arg == member), None
                )
                if argument is None and slot.isdecimal() and int(slot) < len(node.args):
                    argument = node.args[int(slot)]
                supplied = argument is not None
        kind = default
        if supplied:
            kind = resolver.infer(argument, env) if argument is not None else ""
            if env.get(owner + ".@argument-safe." + member) != "safe":
                kind = (
                    "unproven:client"
                    if default in {"client", "unproven:client"}
                    else ""
                )
            if kind != "client":
                kind = (
                    "unproven:client"
                    if annotation in {"client", "unproven:client"}
                    or kind == "unproven:client"
                    else ""
                )
        fields.append(quote(member, safe="") + "=" + quote(kind, safe=""))
    return "instance:" + function[6:] + (ARGUMENTS + ";".join(fields) if fields else "")


def argument_member(parent: str, member: str) -> str | None:
    """Decode only the internally generated field-kind payload, never user input."""
    _, separator, arguments = parent.partition(ARGUMENTS)
    if separator:
        for entry in arguments.split(";"):
            name, _, kind = entry.partition("=")
            if unquote(name) == member:
                return unquote(kind)
    return None
