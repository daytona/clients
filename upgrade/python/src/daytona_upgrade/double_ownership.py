# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Whole-object uses must stay inside one local-double ownership boundary."""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from ._scopes import ScopeWrites


def in_one_line_suite(node: ast.stmt, parents: dict[ast.AST, ast.AST]) -> bool:
    """Simple suites have no statement line on which to append a module self-alias."""
    parent = parents.get(node)
    if sys.version_info >= (3, 10) and isinstance(parent, ast.match_case):
        return parent.pattern.lineno == node.lineno
    return (
        isinstance(
            parent,
            (
                ast.If,
                ast.With,
                ast.AsyncWith,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.ExceptHandler,
            ),
        )
        and parent.lineno == node.lineno
    )


def scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Nested scopes are barriers; their captures are checked separately."""
    yield scope
    if isinstance(
        scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        return
    for child in ast.iter_child_nodes(scope):
        yield from scope_nodes(child)


@dataclass(frozen=True)
class DoubleScope:
    scope: ast.FunctionDef | ast.AsyncFunctionDef
    parents: dict[ast.AST, ast.AST]

    def references(
        self, node: ast.Assign, allowed: set[ast.expr]
    ) -> list[ast.Name] | None:
        """Return member receivers only when every other use is a certified consumer."""
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            return None
        name = target.id
        writes = ScopeWrites()
        for statement in self.scope.body:
            writes.visit(statement)
        if (
            sum(binding == name for binding, _ in writes.events) != 1
            or name in writes.shared
        ):
            return None
        if any(
            arg.arg == name
            for arg in [
                *self.scope.args.posonlyargs,
                *self.scope.args.args,
                *self.scope.args.kwonlyargs,
            ]
        ):
            return None
        attributes: list[ast.Name] = []
        consumed = False
        for reference in (
            child
            for statement in self.scope.body
            for child in ast.walk(statement)
            if isinstance(child, ast.Name) and child.id == name
        ):
            if reference is target:
                continue
            if not isinstance(reference.ctx, ast.Load) or (
                reference.lineno,
                reference.col_offset,
            ) < (
                node.end_lineno or node.lineno,
                node.end_col_offset or node.col_offset,
            ):
                return None
            ancestor = self.parents.get(reference)
            while ancestor is not None and ancestor is not self.scope:
                if isinstance(
                    ancestor,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
                ):
                    if reference not in allowed:
                        return None
                    nested_writes = ScopeWrites()
                    nested_writes.generic_visit(ancestor)
                    if (
                        mutates_namespace(ancestor)
                        or name in nested_writes.shared
                        or any(binding == name for binding, _ in nested_writes.events)
                    ):
                        return None
                    if not isinstance(ancestor, ast.ClassDef) and name in {
                        arg.arg
                        for arg in [
                            *ancestor.args.posonlyargs,
                            *ancestor.args.args,
                            *ancestor.args.kwonlyargs,
                            *([ancestor.args.vararg] if ancestor.args.vararg else []),
                            *([ancestor.args.kwarg] if ancestor.args.kwarg else []),
                        ]
                    }:
                        return None
                ancestor = self.parents.get(ancestor)
            parent = self.parents.get(reference)
            if reference in allowed:
                consumed = True
            elif isinstance(parent, ast.Attribute) and parent.value is reference:
                attributes.append(reference)
            else:
                return None
        return attributes if consumed else None


_NAMESPACE_WRITERS = frozenset({"exec", "eval", "globals", "locals", "vars", "setattr"})


def mutates_namespace(scope: ast.AST) -> bool:
    """`exec("fake = Other()")` can rebind a name that ScopeWrites cannot see."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _NAMESPACE_WRITERS
        for node in ast.walk(scope)
    )
