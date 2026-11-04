# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative binding kills at Python's lexical and control-flow boundaries."""

from __future__ import annotations

import ast
from functools import cached_property

from ._scopes import ScopeWrites
from .annotation_exports import joined_bindings
from .attribute_proofs import AttributeProofs
from .augmented_proofs import augmented_kind
from .collection_proofs import iterable_element
from .prefix_proofs import PrefixProofs
from .proof_mutations import (
    CONTAINER_WRITE,
    invalidate,
    mutates_container,
    shadowed_reflective,
)
from .reachability import falls_through
from .resolution import Resolver, dotted
from .store_names import stored_names


class BindingVisitor(ast.NodeVisitor):
    """Shared binding operations; the concrete visitor owns mutable proof state."""

    env: dict[str, str]
    resolver: Resolver
    type_bindings: dict[str, str]
    deferred: dict[ast.AST, set[str]]
    class_enclosing: dict[str, str]
    class_lexical: dict[str, str]
    class_mutations: set[str]
    in_class_body: bool
    reaching: dict[ast.AST, bool]
    prefix_proofs: tuple[dict[str, str], ...] = ()
    comprehension_writes: tuple[dict[str, str], ...] = ()

    def __init__(self) -> None:
        self.reaching = {}
        self.write_cache: dict[ast.AST, ScopeWrites] = {}
        self.assigned_cache: dict[ast.AST, frozenset[str]] = {}
        self.shadow_cache: dict[ast.AST, frozenset[str]] = {}
        self.reflective_aliases: dict[str, str] = {}

    def scope_writes(self, node: ast.AST) -> ScopeWrites:
        """Scan a lexical body once, including across speculative loop visitors."""
        if node not in self.write_cache:
            writes = ScopeWrites(self.reflective_trusted, self.reflective_aliases)
            writes.generic_visit(node)
            self.write_cache[node] = writes
        return self.write_cache[node]

    def reflective_trusted(self, node: ast.AST) -> bool:
        """Trust a reflective store only if no module binding shadows its builtin."""
        module = getattr(self, "module", None)
        if module is None:
            return False
        if module not in self.shadow_cache:
            self.shadow_cache[module] = shadowed_reflective(module)
        # A qualified builtins.<name> call is immune to a module-level rebinding.
        called = {
            dotted(child.func).split(".")[0]
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and not dotted(child.func).startswith("builtins.")
        }
        return not called & self.shadow_cache[module] and AttributeProofs(
            self.resolver, self.env
        ).trusted(node)

    def assigned_names(self, node: ast.AST) -> frozenset[str]:
        """Reuse loop/function assigned-name sets in normal and preview visits."""
        if node not in self.assigned_cache:
            self.assigned_cache[node] = (
                stored_names(
                    ast.Module(body=node.body, type_ignores=[]), self.reflective_aliases
                )
                if isinstance(node, (ast.For, ast.AsyncFor, ast.While))
                else frozenset(name for name, _ in self.scope_writes(node).events)
            )
        return self.assigned_cache[node]

    def visit(self, node: ast.AST) -> None:
        super().visit(node)
        # Observe every prefix, including nested writes restored by a scope exit.
        self.prefix_observer.observe(self.prefix_proofs, self.env)

    @cached_property
    def prefix_observer(self) -> PrefixProofs:
        return PrefixProofs(self.resolver)

    def annotation_env(self) -> dict[str, str]:
        return {**self.env, **self.type_bindings}

    def fence(self, name: str) -> None:
        """Forget certainty, not linked evidence, at an opaque write boundary."""
        before = self.annotation_env()
        if name == "*":
            self.env.clear()
            self.type_bindings.clear()
        else:
            invalidate(self.env, name)
            self.type_bindings.pop(name, None)
        self.env = joined_bindings(self.resolver, (before, self.env))

    def visit_suite(self, statements: list[ast.stmt]) -> bool:
        """An abrupt exit makes the rest of this statement suite unreachable."""
        for statement in statements:
            self.visit(statement)
            if not self.reaching.get(statement, falls_through([statement])):
                return False
        return True

    def bind(self, target: ast.AST, kind: str) -> None:
        AttributeProofs(self.resolver, self.env, self.reflective_aliases).bind(
            target, kind
        )

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.visit(target)
            self.bind(target, "")

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        previous = self.resolver.infer(node.target, self.env)
        self.visit(node.target)
        self.visit(node.value)
        kind = augmented_kind(
            previous, node, lambda value: self.resolver.infer(value, self.env)
        )
        invalidate(self.env, CONTAINER_WRITE)
        self.bind(node.target, kind)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            invalidate(self.env, node.name)
        self.reaching[node] = self.visit_suite(node.body)
        if node.name:
            invalidate(self.env, node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *(d for d in node.args.kw_defaults if d)]:
            self.visit(default)
        outer = self.env.copy()
        if self.in_class_body:
            self.env = self.class_lexical.copy()
        class_body = self.in_class_body
        self.in_class_body = False
        for name in self.deferred.get(node, set()):
            self.fence(name)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            invalidate(self.env, arg.arg)
        for arg in [node.args.vararg, node.args.kwarg]:
            if arg:
                invalidate(self.env, arg.arg)
        writes = self.comprehension_writes
        self.comprehension_writes = ()
        self.visit(node.body)
        self.comprehension_writes = writes
        self.env = outer
        self.in_class_body = class_body
        if any(mutates_container(child) for child in ast.walk(node.body)):
            invalidate(self.env, CONTAINER_WRITE)

    def comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        first = node.generators[0]
        # An empty literal has no iterable effects and cannot execute any stores.
        if isinstance(first.iter, (ast.List, ast.Tuple)) and not first.iter.elts:
            return
        outer = self.env.copy()
        walrus: dict[str, str] = {}
        self.comprehension_writes = (*self.comprehension_writes, walrus)
        class_body = self.in_class_body
        if any(mutates_container(child) for child in ast.walk(node)):
            invalidate(self.env, CONTAINER_WRITE)
        self.visit(first.iter)
        first_kind = self.resolver.infer(first.iter, self.env)
        if class_body:
            container_write = CONTAINER_WRITE in self.env
            self.env = self.class_lexical.copy()
            if container_write:
                invalidate(self.env, CONTAINER_WRITE)
        self.in_class_body = False
        for name in self.deferred.get(node, set()):
            invalidate(self.env, name)
        if first_kind.startswith("items:") and CONTAINER_WRITE in self.env:
            first_kind = ""
        for generator in node.generators:
            if generator is first:
                kind = first_kind
            else:
                self.visit(generator.iter)
                kind = self.resolver.infer(generator.iter, self.env)
            self.visit(generator.target)
            self.bind(generator.target, iterable_element(kind))
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.env = outer.copy()
        self.comprehension_writes = self.comprehension_writes[:-1]
        self.in_class_body = class_body
        # External stores may execute zero times; never restore their old proofs.
        writes = ScopeWrites(aliases=self.reflective_aliases)
        writes.visit(node)
        for name, _ in writes.events:
            self.fence(name)
        for name, kind in walrus.items():
            self.env = joined_bindings(
                self.resolver,
                ({**self.env, name: outer.get(name, "")}, {**self.env, name: kind}),
            )

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension

    def kill_stores(self, node: ast.AST) -> None:
        # Rebinding a parent removes its old type before alias-compatible member kills.
        for name in sorted(
            stored_names(node, self.reflective_aliases),
            key=lambda name: name.count("."),
        ):
            self.fence(name)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # Future operand stores can rebind an alias before mutating its member.
        # Fence those proofs up front, but retain client uncertainty at the join.
        before = self.env.copy()
        self.kill_stores(node)
        self.env = joined_bindings(self.resolver, (before, self.env))
        exits: dict[str, str] | None = None
        for value in node.values:
            self.visit(value)
            exits = joined_bindings(self.resolver, (exits, self.env.copy()))
        self.env = exits if exits is not None else self.env

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.kill_stores(node)
        for value in (node.test, node.body, node.orelse):
            self.visit(value)
            self.kill_stores(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        type_checking = (
            self.resolver.infer(node.test, self.env) == "typing:TYPE_CHECKING"
        )
        before = self.env.copy()
        then_reaches = self.visit_suite(node.body)
        then = self.env.copy()
        self.env = before.copy()
        else_reaches = self.visit_suite(node.orelse)
        self.reaching[node] = then_reaches or else_reaches
        if type_checking:
            # Typing imports can prove annotations, never runtime constructors.
            self.type_bindings.update(
                {key: value for key, value in then.items() if before.get(key) != value}
            )
        else:
            self.env = joined_bindings(
                self.resolver,
                (then if then_reaches else None, self.env if else_reaches else None),
            )
