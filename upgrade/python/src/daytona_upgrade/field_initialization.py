# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Recognize framework-generated stores only without transformation hooks."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .controlflow import BindingVisitor

ATTRS_DECORATORS: Final = frozenset(
    f"module:{module}.{name}"
    for module in ("attr", "attrs")
    for name in ("define", "frozen", "mutable", "s", "attrs")
)
FIELD_HELPERS: Final = {
    "call:dataclasses.field": "dataclass",
    "module:attrs.field": "attrs",
    "module:attr.field": "attrs",
    "module:attr.ib": "attrs",
    "module:attr.attrib": "attrs",
    "module:pydantic.Field": "pydantic",
}


def framework_init(node: ast.ClassDef, enclosing: BindingVisitor) -> str:
    """Unknown bases, decorators, validators and post-init code require review."""
    resolver, env = enclosing.resolver, enclosing.env
    decorators = [
        resolver.infer(d.func if isinstance(d, ast.Call) else d, env)
        for d in node.decorator_list
    ]
    framework = (
        "attrs"
        if decorators
        and all(kind in ATTRS_DECORATORS for kind in decorators)
        and not node.bases
        else "pydantic"
        if not decorators
        and len(node.bases) == 1
        and resolver.infer(node.bases[0], env) == "module:pydantic.BaseModel"
        else ""
    )
    if not framework or node.keywords:
        return ""
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and (
            decorator.args
            or any(
                kw.arg
                not in {
                    "auto_attribs",
                    "slots",
                    "frozen",
                    "kw_only",
                    "repr",
                    "eq",
                    "order",
                    "hash",
                }
                or not isinstance(kw.value, ast.Constant)
                or type(kw.value.value) is not bool
                for kw in decorator.keywords
            )
        ):
            return ""
    for statement in node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.decorator_list or statement.name.startswith(("__", "model_")):
                return ""
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if any(
                not isinstance(target, ast.Name)
                or target.id.startswith("__")
                or target.id in {"model_fields", "model_computed_fields"}
                for target in targets
            ):
                return ""
            if any(
                isinstance(target, ast.Name) and target.id == "model_config"
                for target in targets
            ):
                config = statement.value
                if not (
                    isinstance(config, ast.Call)
                    and resolver.infer(config.func, env) == "module:pydantic.ConfigDict"
                    and not config.args
                    and all(
                        kw.arg == "arbitrary_types_allowed"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                        for kw in config.keywords
                    )
                ):
                    return ""
        elif not (
            isinstance(statement, ast.Pass)
            or isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
        ):
            return ""
    return framework
