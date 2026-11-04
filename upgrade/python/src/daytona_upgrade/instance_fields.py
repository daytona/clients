# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Separate default-factory instance fields from class attribute lookup."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from .field_arguments import ARGUMENTS, argument_member

if TYPE_CHECKING:
    from .controlflow import BindingVisitor
    from .resolution import Resolver


def generated_init(node: ast.ClassDef, enclosing: BindingVisitor) -> bool:
    """Exclude user constructors, bases and decorators that can replace initialization."""
    return (
        not node.bases
        and not node.keywords
        and not any(
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and statement.name in {"__init__", "__new__"}
            for statement in node.body
        )
        and all(
            enclosing.resolver.infer(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                enclosing.env,
            )
            == "call:dataclasses.dataclass"
            and not (
                isinstance(decorator, ast.Call)
                and any(
                    keyword.arg == "init"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                    for keyword in decorator.keywords
                )
            )
            for decorator in node.decorator_list
        )
    )


def instance_member(
    node: ast.Attribute, resolver: Resolver, env: dict[str, str]
) -> str:
    """Read constructor-specific field values before class-level defaults."""
    parent = resolver.infer(node.value, env)
    owner = parent.split(":")[1]
    value = env.get(owner + "." + node.attr, env.get(owner + ".*", ""))
    if parent.endswith(":review"):
        value = value.removeprefix("property:")
        return (
            ("fn:" if value.startswith("fn:") else "") + "unproven:client"
            if value in {"client", "unproven:client", "fn:client", "fn:unproven:client"}
            else ""
        )
    if parent.startswith("instance:"):
        argument = argument_member(parent, node.attr)
        if argument is not None:
            return argument
        if (
            isinstance(node.value, ast.Call)
            and resolver.infer(node.value.func, env).split(":")[:2] == ["class", owner]
            and not node.value.args
            and not node.value.keywords
            and owner + ".@factory." + node.attr in env
        ):
            return env[owner + ".@factory." + node.attr]
        return value.removeprefix("property:")
    return value


def exported_classes(bindings: dict[str, str], module: str) -> dict[str, str]:
    """Qualify class owners and copy their complete field metadata for importers."""
    owners = {
        kind.split(":")[1]
        for kind in bindings.values()
        if kind.startswith("class:") and "." not in kind.split(":")[1]
    }
    return {
        **bindings,
        **{
            name: "class:" + module + "." + kind.removeprefix("class:")
            for name, kind in bindings.items()
            if kind.startswith("class:") and kind.split(":")[1] in owners
        },
        **{
            module + "." + name: kind
            for name, kind in bindings.items()
            if name.partition(".")[0] in owners and "." in name
        },
    }


def imported_class_fields(
    exports: dict[str, str], mutations: frozenset[str]
) -> dict[str, str]:
    """Import only qualified metadata, never another module's lexical bindings."""
    from .class_field_mutations import demote_class_fields

    prefixes = tuple(
        kind.split(":")[1] + "."
        for kind in exports.values()
        if kind.startswith("class:")
    )
    fields = {name: kind for name, kind in exports.items() if name.startswith(prefixes)}
    for prefix in prefixes:
        demote_class_fields(fields, prefix[:-1], mutations)
    return fields


def exported_instance(kind: str, env: dict[str, str], module: str) -> str:
    """Carry class-field links across imports without certifying mutable receivers."""
    for prefix in ("fn:", "annotation:"):
        if kind.startswith(prefix + "parameter-instance:"):
            return (
                prefix
                + "parameter-"
                + exported_instance(
                    kind.removeprefix(prefix + "parameter-"), env, module
                )
            )
        if kind.startswith(prefix + "instance:"):
            return prefix + exported_instance(kind.removeprefix(prefix), env, module)
    if not kind.startswith("instance:"):
        return kind
    owner = kind.split(":")[1]
    qualified = (
        kind
        if "." in owner
        else "instance:" + module + "." + kind.removeprefix("instance:")
    )
    fields: dict[str, str] = {}
    for name, value in env.items():
        if not name.startswith(owner + "."):
            continue
        member = name.removeprefix(owner + ".")
        if not member.isidentifier():
            continue
        argument = argument_member(kind, member)
        value = (value if argument is None else argument).removeprefix("property:")
        if value in {"client", "unproven:client"}:
            fields[member] = "unproven:client"
    if not fields:
        return qualified
    # Retain explicit per-instance overrides, including unrelated argument values.
    base, separator, arguments = qualified.partition(ARGUMENTS)
    encoded = arguments.split(";") if separator else []
    encoded = [
        entry for entry in encoded if unquote(entry.partition("=")[0]) not in fields
    ]
    encoded.extend(
        quote(name, safe="") + "=" + quote(value, safe="")
        for name, value in fields.items()
    )
    return base + ARGUMENTS + ";".join(encoded)
