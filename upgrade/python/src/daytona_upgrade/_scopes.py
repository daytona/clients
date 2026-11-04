# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lexical writes which can invalidate a deferred function's inherited proofs."""

from __future__ import annotations

import ast
import sys
from collections import Counter
from collections.abc import Callable

from .bindings import indirect_writes
from .proof_mutations import CONTAINER_WRITE, mutates_container
from .resolution import dotted


class ScopeWrites(ast.NodeVisitor):
    """Collect binding events without entering a nested lexical scope."""

    def __init__(
        self,
        trusted: Callable[[ast.AST], bool] | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        # Reflective instance-dictionary stores fence containers unless the
        # caller proves the builtin they use (see AttributeProofs.trusted).
        self.trusted = trusted
        self.aliases = aliases
        self.events: list[tuple[str, tuple[int, int]]] = []
        self.nested: list[ast.stmt | ast.expr] = []
        self.shared: set[str] = set()

    def mutates(self, node: ast.AST) -> bool:
        return mutates_container(
            node, trusted_reflective=self.trusted is not None and self.trusted(node)
        )

    def visit_Global(self, node: ast.Global | ast.Nonlocal) -> None:
        self.shared.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_Call(self, node: ast.Call) -> None:
        for name in indirect_writes(node, self.aliases):
            self.events.append((name, (node.lineno, node.col_offset)))
        self.generic_visit(node)

    def visit(self, node: ast.AST) -> None:
        if isinstance(node, (ast.stmt, ast.expr)) and self.mutates(node):
            self.events.append((CONTAINER_WRITE, (node.lineno, node.col_offset)))
        # Match AST nodes are unavailable on the supported Python 3.9 floor.
        if sys.version_info >= (3, 10):
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                self.events.append((node.name, (node.lineno, node.col_offset)))
            if isinstance(node, ast.MatchMapping) and node.rest:
                self.events.append((node.rest, (node.lineno, node.col_offset)))
        super().visit(node)

    def record_target(self, target: ast.AST, position: tuple[int, int]) -> None:
        for child in ast.walk(target):
            if self.mutates(child):
                self.events.append((CONTAINER_WRITE, position))
            if isinstance(child, ast.Subscript) and isinstance(
                child.ctx, (ast.Store, ast.Del)
            ):
                self.visit_Subscript(child)
            if isinstance(child, (ast.Name, ast.Attribute)) and isinstance(
                child.ctx, (ast.Store, ast.Del)
            ):
                self.events.append((dotted(child), position))

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        # The store happens after the RHS, including any lambda it constructs.
        position = (
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        )
        for target in node.targets:
            self.record_target(target, position)

    def visit_AnnAssign(
        self, node: ast.AnnAssign | ast.NamedExpr | ast.AugAssign
    ) -> None:
        if node.value:
            self.visit(node.value)
        self.record_target(
            node.target,
            (node.end_lineno or node.lineno, node.end_col_offset or node.col_offset),
        )

    visit_NamedExpr = visit_AnnAssign
    visit_AugAssign = visit_AnnAssign

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.events.append((node.id, (node.lineno, node.col_offset)))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.events.append((dotted(node), (node.lineno, node.col_offset)))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            if names := indirect_writes(node, self.aliases):
                for name in names:
                    self.events.append((name, (node.lineno, node.col_offset)))
                    if name.endswith(".*"):
                        self.events.append((name[:-2], (node.lineno, node.col_offset)))
                self.generic_visit(node)
                return
            # A custom __setitem__ can replace attributes on the same owner.
            owner = dotted(node.value)
            if owner:
                # Preserve mutation provenance for alias-compatible member kills;
                # the bare root event also invalidates the target's own binding.
                self.events.append((owner + ".*", (node.lineno, node.col_offset)))
                self.events.append((owner, (node.lineno, node.col_offset)))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.events.append((name, (node.lineno, node.col_offset)))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.events.append(
                (alias.asname or alias.name, (node.lineno, node.col_offset))
            )

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.events.append((node.name, (node.lineno, node.col_offset)))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.nested.append(node)
        self.events.append((node.name, (node.lineno, node.col_offset)))
        for expression in node.decorator_list:
            self.visit(expression)
        self.defaults(node.args)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.nested.append(node)
        self.defaults(node.args)

    def defaults(self, arguments: ast.arguments) -> None:
        for default in [*arguments.defaults, *(d for d in arguments.kw_defaults if d)]:
            self.visit(default)

    def comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        # Only names are comprehension-local; attributes/subscripts mutate owners.
        local = {
            child.id
            for generator in node.generators
            for child in ast.walk(generator.target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }
        for generator in node.generators:
            self.visit(generator.iter)
            writes = ScopeWrites(aliases=self.aliases)
            writes.visit(generator.target)
            self.events.extend(
                # The element appears before the target in source but runs after it.
                (name, (node.lineno, node.col_offset))
                for name, _ in writes.events
                if name.split(".")[0] not in local
            )
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.nested.append(node)
        self.events.append((node.name, (node.lineno, node.col_offset)))
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)


class DeferredWrites(ast.NodeVisitor):
    """Index future enclosing writes before sequential analysis visits each body."""

    def __init__(
        self, module: ast.Module, aliases: dict[str, str] | None = None
    ) -> None:
        self.scopes: list[list[tuple[str, tuple[int, int]]]] = []
        self.aliases = aliases
        self.result: dict[ast.AST, set[str]] = {}
        # Explicit global/nonlocal mutations can happen in any call order.
        self.shared = {
            name
            for child in ast.walk(module)
            if isinstance(child, (ast.Global, ast.Nonlocal))
            for name in child.names
        }
        self.enter(module)

    def enter(self, node: ast.AST) -> None:
        writes = ScopeWrites(aliases=self.aliases)
        writes.generic_visit(node)
        # A sole unconditional declaration is a forward factory, not a rebinding.
        definitions = (
            {
                statement.name
                for statement in node.body
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef))
            else set()
        )
        counts = Counter(name for name, _ in writes.events)
        self.scopes.append(
            [
                (name, position)
                for name, position in writes.events
                if name not in definitions or counts[name] != 1
            ]
        )
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.GeneratorExp,
    ) -> None:
        position = (node.lineno, node.col_offset)
        self.result[node] = self.shared | {
            name
            for events in self.scopes
            for name, written_at in events
            if written_at > position
        }
        if "*" in self.result[node]:
            # A future wildcard can replace every inherited name, not a name '*'.
            # Include referenced parameters/builtins as well as recorded stores.
            self.result[node].update(
                name for events in self.scopes for name, _ in events
            )
            self.result[node].update(
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            )
        self.enter(node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef
    visit_GeneratorExp = visit_FunctionDef
