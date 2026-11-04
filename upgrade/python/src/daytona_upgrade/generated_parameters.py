# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor slots for supported framework-generated initializers."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .field_initialization import FIELD_HELPERS

if TYPE_CHECKING:
    from .classflow import AttributeWrites


def generated_parameters(node: ast.ClassDef, writes: AttributeWrites) -> dict[str, str]:
    """Keep excluded/pseudo-fields out of positions; retain keyword-only inputs."""
    if writes.generated_init:
        prefix = node.name + ".@parameter."
        slots = {
            name.removeprefix(prefix): slot
            for name, slot in writes.class_enclosing.items()
            if name.startswith(prefix)
        }
        return {
            name: "*=any" if slot == "unknown" else slot for name, slot in slots.items()
        }
    framework = writes.field_framework
    if framework not in {"attrs", "pydantic"}:
        return {}
    keyword_only = framework == "pydantic" or any(
        isinstance(decorator, ast.Call)
        and any(
            kw.arg == "kw_only"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in decorator.keywords
        )
        for decorator in node.decorator_list
    )
    slots: dict[str, str] = {}
    position = 0
    uncertain = False
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
                uncertain = True
        annotation_kind = writes.resolver.infer(
            annotation.value if isinstance(annotation, ast.Subscript) else annotation,
            writes.class_lexical,
        )
        if annotation_kind in {
            "module:typing.ClassVar",
            "module:typing_extensions.ClassVar",
        }:
            continue
        options = (
            {kw.arg: kw.value for kw in statement.value.keywords}
            if isinstance(statement.value, ast.Call)
            and FIELD_HELPERS.get(
                writes.resolver.infer(statement.value.func, writes.class_lexical)
            )
            == framework
            else {}
        )
        init = options.get("init")
        # BaseModel's init/exclude options do not remove constructor keywords.
        if (
            framework == "attrs"
            and isinstance(init, ast.Constant)
            and init.value is False
        ):
            continue
        kw_only = options.get("kw_only")
        uncertain |= None in options or any(
            value is not None
            and not (isinstance(value, ast.Constant) and type(value.value) is bool)
            for value in (init, kw_only)
        )
        field_keyword = framework == "pydantic" or (
            keyword_only
            if kw_only is None
            else isinstance(kw_only, ast.Constant) and kw_only.value is True
        )
        name = statement.target.id
        alias = options.get("alias")
        if isinstance(alias, ast.Constant) and isinstance(alias.value, str):
            name = alias.value
        else:
            uncertain |= alias is not None
            if framework == "attrs":
                name = name.lstrip("_")
        slots[statement.target.id] = (
            name + "=" + ("keyword" if field_keyword else str(position))
        )
        if not field_keyword:
            position += 1
    return {name: "*=any" if uncertain else slot for name, slot in slots.items()}
