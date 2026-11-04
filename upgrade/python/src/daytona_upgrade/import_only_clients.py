# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sole-writer SDK imports can certify tuple constructors even after ImportError joins."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

from ._scopes import ScopeWrites
from .expression_names import dotted
from .reflective_names import canonical_name, namespace_writer, reflective_names

if TYPE_CHECKING:
    from .resolution import Resolver

IMPORT_ONLY: Final = "@import-only-client."


def import_only_clients(tree: ast.Module, resolver: Resolver) -> dict[str, str]:
    """Require a runtime import and no alternative module/global binding or opaque write."""
    if not any(isinstance(statement, ast.Try) for statement in tree.body):
        return {}
    nodes = tuple(ast.walk(tree))
    aliases = reflective_names(tree)
    parents = {
        child: parent for parent in nodes for child in ast.iter_child_nodes(parent)
    }
    imports: dict[str, set[tuple[int, int]]] = {}
    runtime: set[str] = set()
    fenced = {"exec", "eval", "globals", "locals", "vars", "setattr", "__setattr__"}
    # `from builtins import exec as run` hides a namespace writer behind an alias.
    builtin_aliases = {
        alias.asname or alias.name
        for node in nodes
        if isinstance(node, ast.ImportFrom) and node.module == "builtins"
        for alias in node.names
        if alias.name in fenced or alias.name == "*"
    }
    for node in nodes:
        if isinstance(node, ast.Call) and namespace_writer(node.func, aliases):
            return {}
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and (node.func.id in builtin_aliases or "*" in builtin_aliases)
        ):
            return {}
        if (
            isinstance(node, ast.Call)
            and canonical_name(node.func, aliases).split(".")[-1] in fenced
            # SDK process.exec is not Python's namespace-mutating builtin.
            and not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "exec"
                and canonical_name(node.func.value, aliases) != "builtins"
            )
        ):
            return {}
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            # Even an alias of the dictionary can replace a module import later.
            return {}
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        ancestors: list[ast.AST] = []
        parent = parents.get(node)
        while parent is not None:
            ancestors.append(parent)
            parent = parents.get(parent)
        if any(
            isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for ancestor in ancestors
        ):
            continue
        for alias in node.names:
            if (
                node.module not in resolver.mapping.modules
                or resolver.index.owns(node.module)
                or resolver.imported(node.module, alias.name) != "type:client"
            ):
                continue
            name = alias.asname or alias.name
            imports.setdefault(name, set()).add((node.lineno, node.col_offset))
            if not any(isinstance(ancestor, ast.If) for ancestor in ancestors) and any(
                isinstance(ancestor, ast.Try)
                and any(
                    dotted(handler.type) == "ImportError"
                    for handler in ancestor.handlers
                    if handler.type is not None
                )
                for ancestor in ancestors
            ):
                runtime.add(name)
    writes = ScopeWrites()
    writes.visit(tree)
    if any(binding == "*" for binding, _ in writes.events):
        return {}
    for node in nodes:
        if isinstance(node, ast.Global):
            # Class suites and functions nested inside them can share module bindings
            # too; `nonlocal` only rebinds an enclosing function's cell, never the module.
            runtime.difference_update(node.names)
        if isinstance(node, ast.Attribute) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            # A module alias (including sys.modules[__name__]) may own this name.
            runtime.discard(node.attr)
    return {
        IMPORT_ONLY + name: "type:client"
        for name in runtime
        if all(
            position in imports[name]
            for binding, position in writes.events
            if binding == name
        )
    }
