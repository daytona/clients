# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Local fake classes can retain identity through a read-only v0 self-alias."""

from __future__ import annotations

import ast

from .local_double_targets import DoubleTargets
from .subclasses import hierarchy


def class_factories(targets: DoubleTargets) -> dict[ast.Call, ast.ClassDef]:
    """Reject opaque construction, namespace hooks, class escapes and imported types."""
    flow = targets.flow
    nodes = tuple(ast.walk(flow.module))
    parents = {
        child: parent for parent in nodes for child in ast.iter_child_nodes(parent)
    }
    result: dict[ast.Call, ast.ClassDef] = {}
    for cls in (node for node in nodes if isinstance(node, ast.ClassDef)):
        if (
            cls.bases
            or cls.keywords
            or cls.decorator_list
            or "property" in flow.context.index.builtin_writes
        ):
            continue
        if any(
            (
                isinstance(node, ast.Name)
                and node.id == "property"
                and isinstance(node.ctx, ast.Store)
            )
            or isinstance(node, ast.arg)
            and node.arg == "property"
            or isinstance(node, ast.alias)
            and (node.asname or node.name) == "property"
            or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == "property"
            for node in nodes
        ):
            continue
        methods = [
            node
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if any(
            method.name
            in {
                "__new__",
                "__getattr__",
                "__getattribute__",
                "__setattr__",
                "__slots__",
                "v2",
            }
            or method.decorator_list
            and method.name != "v0"
            or not (method.args.posonlyargs or method.args.args)
            or [*method.args.posonlyargs, *method.args.args][0].arg != "self"
            for method in methods
        ):
            continue
        # A preexisting namespace is compatible only if it is precisely our self property.
        if any(
            isinstance(node, ast.Attribute)
            and (
                node.attr == "__dict__"
                or node.attr == "v0"
                and isinstance(node.ctx, (ast.Store, ast.Del))
            )
            for node in ast.walk(cls)
        ):
            continue
        v0 = [
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "v0"
        ]
        if any(method.name == "v0" and method not in v0 for method in methods):
            continue
        if v0 and not (
            len(v0) == 1
            and len(v0[0].decorator_list) == 1
            and isinstance(v0[0].decorator_list[0], ast.Name)
            and v0[0].decorator_list[0].id == "property"
            and len(v0[0].body) == 1
            and isinstance(v0[0].body[0], ast.Return)
            and isinstance(v0[0].body[0].value, ast.Name)
            and v0[0].body[0].value.id == "self"
        ):
            continue
        self_return = v0[0].body[0] if v0 else None
        if any(
            isinstance(node, ast.Name)
            and node.id == "self"
            and isinstance(node.ctx, ast.Load)
            and not isinstance(parents.get(node), ast.Attribute)
            and not (isinstance(self_return, ast.Return) and node is self_return.value)
            for node in ast.walk(cls)
        ):
            continue
        if any(
            not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass))
            and not (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            )
            for node in cls.body
        ):
            continue
        identity = f"{flow.context.module}.{cls.name}.{cls.lineno}.{cls.col_offset}"
        if any(
            kind.startswith("class:") and hierarchy(kind)[0].identity == identity
            for module, exports in flow.context.index.exports.items()
            if module != flow.context.module
            for kind in exports.values()
        ):
            continue
        references = [
            node
            for node in nodes
            if isinstance(node, ast.Name)
            and targets.kind(node).startswith("class:")
            and hierarchy(targets.kind(node))[0].identity == identity
        ]
        calls: list[ast.Call] = []
        for reference in references:
            parent = parents.get(reference)
            if not isinstance(parent, ast.Call) or parent.func is not reference:
                break
            calls.append(parent)
        else:
            result.update((call, cls) for call in calls)
    return result
