# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Module-wide lexical binding facts for suggestion-only workflow membership."""

from __future__ import annotations

import ast

from ._scopes import ScopeWrites


class WorkflowScopes:
    """Cache lexical owners and rebinding events once for each lexical scope.

    AST dispatch uses isinstance because this CLI supports Python 3.9.
    """

    def __init__(self, module: ast.Module) -> None:
        self.module = module
        self.nodes = tuple(ast.walk(module))
        self.parents = {
            child: parent
            for parent in self.nodes
            for child in ast.iter_child_nodes(parent)
        }
        self.locations: dict[ast.AST, ast.AST] = {}
        self.writes: dict[ast.AST, ScopeWrites] = {}
        self.bindings: dict[ast.AST, set[str]] = {}
        self.globals: dict[ast.AST, set[str]] = {}
        for node in self.nodes:
            if not isinstance(
                node,
                (
                    ast.Module,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.Lambda,
                    ast.ClassDef,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ):
                continue
            writes = ScopeWrites()
            writes.generic_visit(node)
            self.writes[node] = writes
            names = {name for name, _ in writes.events if "." not in name}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                names.update(
                    arg.arg
                    for arg in [
                        *node.args.posonlyargs,
                        *node.args.args,
                        *node.args.kwonlyargs,
                        *(a for a in (node.args.vararg, node.args.kwarg) if a),
                    ]
                )
            if isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                names.update(
                    child.id
                    for generator in node.generators
                    for child in ast.walk(generator.target)
                    if isinstance(child, ast.Name)
                )
            self.bindings[node] = names - writes.shared
            self.globals[node] = set()
        for node in self.nodes:
            if isinstance(node, ast.Global):
                self.globals[self.scope(node)].update(node.names)

    def scope(self, node: ast.AST) -> ast.AST:
        """Locate evaluation scope, not the syntactic container of a default."""
        if node in self.locations:
            return self.locations[node]
        child = node
        parent = self.parents.get(node)
        while parent is not None:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                body = parent.body
                if child is not body and not (isinstance(body, list) and child in body):
                    child, parent = parent, self.parents.get(parent)
                    continue
            if isinstance(parent, ast.comprehension) and child is parent.iter:
                comprehension = self.parents[parent]
                if (
                    isinstance(
                        comprehension,
                        (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
                    )
                    and parent is comprehension.generators[0]
                ):
                    child, parent = comprehension, self.parents.get(comprehension)
                    continue
            if parent in self.writes:
                self.locations[node] = parent
                return parent
            child, parent = parent, self.parents.get(parent)
        self.locations[node] = self.module
        return self.module

    def owner(self, name: str, node: ast.AST) -> ast.AST:
        """Resolve a root name lexically, respecting locals, globals and nonlocals."""
        return self.binding_owner(name, self.scope(node))

    def binding_owner(self, name: str, scope: ast.AST) -> ast.AST:
        """Resolve a binding from a known scope, including its shared declarations."""
        skip_classes = False
        while True:
            if not (skip_classes and isinstance(scope, ast.ClassDef)):
                if name in self.globals[scope]:
                    return self.module
                if name in self.bindings[scope]:
                    return scope
            if scope is self.module:
                return scope
            skip_classes |= isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            )
            scope = self.scope(scope)

    def deferred(self, node: ast.AST, owner: ast.AST) -> bool:
        """Whether reaching the binding's scope crosses a deferred function body."""
        scope = self.scope(node)
        while scope is not owner and scope is not self.module:
            if isinstance(
                scope,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.GeneratorExp),
            ):
                return True
            scope = self.scope(scope)
        return False
