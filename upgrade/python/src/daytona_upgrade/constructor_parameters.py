# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Review-only injection contracts for holders with proven client fields."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .generated_parameters import generated_parameters

if TYPE_CHECKING:
    from .classflow import AttributeWrites


def constructor_parameters(node: ast.ClassDef, writes: AttributeWrites) -> str:
    """A stored double is not exclusively owned; never authorize its wrapping."""
    fields = {
        name.removeprefix("self.")
        for name, kinds in writes.writes.items()
        if kinds == {"client"}
    }
    initializers = [
        statement
        for statement in node.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "__init__"
    ]
    if len(initializers) == 1:
        initializer = initializers[0]
        positional = [*initializer.args.posonlyargs, *initializer.args.args]
        if not positional or positional[0].arg != "self" or initializer.decorator_list:
            return ""
        supplied = {
            child.id
            for statement in ast.walk(initializer)
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            and statement.value is not None
            for target in (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr in fields
            for child in ast.walk(statement.value)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        env = writes.definition_envs[initializer]
        return ";".join(
            f"{'' if arg in initializer.args.posonlyargs else arg.arg}=!{index - 1 if arg in positional else 'keyword'}"
            for index, arg in enumerate([*positional, *initializer.args.kwonlyargs])
            if index
            and arg.arg in supplied
            and writes.resolver.annotation(arg.annotation, env) == "client"
        )
    if initializers:
        return ""
    if writes.generated_init:
        # Direct dataclass field lookups already use argument-sensitive proofs.
        # These contracts cover method consumers of factory/required stores only.
        fields &= writes.factory_fields | {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is None
        }
        fields &= {
            value.value.attr
            for statement in node.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            for value in ast.walk(statement)
            if isinstance(value, ast.Attribute)
            and value.attr in {*writes.resolver.mapping.moved, "v0"}
            and isinstance(value.value, ast.Attribute)
            and isinstance(value.value.value, ast.Name)
            and value.value.value.id == "self"
        }
    parameters = generated_parameters(node, writes)
    slots: list[str] = []
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(
            statement.target, ast.Name
        ):
            continue
        name = statement.target.id
        slot = parameters.get(name, "excluded")
        if name in fields and slot != "excluded":
            parameter, separator, position = slot.partition("=")
            slots.append(
                f"{parameter if separator else name}=!{position if separator else slot}"
            )
    return ";".join(slots)
