# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Scope-aware, sequential receiver proof and cross-module export indexing."""

from __future__ import annotations

import ast
import sys
from copy import copy

from ._member_write_flow import member_write_effects
from ._project_index_model import SourceContext as SourceContext
from ._scopes import DeferredWrites
from .annotation_exports import joined_bindings
from .annotation_flow import record_annotations
from .call_sites import Call as Call
from .call_sites import record_arguments, record_factory_reference
from .class_field_mutations import record_class_mutations
from .classflow import class_bindings
from .dockerfile_images import passes_dockerfile_image
from .field_effects import apply_effects, escape_fields, precise_write_arguments
from .field_safety import class_mutations
from .importflow import ImportVisitor
from .member_mutations import receiver_kind, remember_receiver
from .mock_inference import MockInference
from .mock_links import mock_linked
from .mock_spec_origins import MockSpecOrigins
from .project_index import build_index as build_index
from .proof_mutations import invalidate
from .pytest_collection import matches_name
from .reflective_names import reflective_names
from .resolution import (
    Resolver,
    dotted,
)
from .source_text import SourceText
from .subclasses import client_constructor, constructor_kind


class Flow(ImportVisitor):
    """Accumulate proofs at expression positions, never infer by variable spelling."""

    def __init__(self, resolver: Resolver, context: SourceContext) -> None:
        super().__init__()
        self.resolver = resolver
        self.context = context
        # The methods whose calls may take a 0.x Dockerfile image.
        self.dockerfile_methods = frozenset(
            suggestion.method
            for suggestion in resolver.mapping.suggestions
            if suggestion.dockerfile_form is not None and suggestion.method is not None
        )
        self.env: dict[str, str] = {
            key: value
            for key, value in context.index.exports.get(context.module, {}).items()
            if value.startswith("fn:") or key.startswith(("@global.", "@fixture."))
        }
        self.env.setdefault("setattr", "call:builtins.setattr")
        self.env["@module"] = context.module
        # Unimported spellings are review-only; they never certify string edits.
        self.env.setdefault("patch", "unbound:patch")
        self.env.setdefault("mock", "module:unbound")
        self.env.setdefault("mocker", "module:pytest_mock.MockerFixture")
        self.env.setdefault("monkeypatch", "module:pytest.MonkeyPatch")
        if "open" not in context.index.builtin_writes:
            self.env.setdefault("open", "module:builtins.open")
        for reader in ("print", "repr", "str", "getattr", "hasattr"):
            self.env.setdefault(reader, "call:builtins." + reader)
        for decorator in ("staticmethod", "classmethod", "property"):
            self.env.setdefault(decorator, "call:builtins." + decorator)
        self.kinds: dict[tuple[int, int, int, int], str] = {}
        self.mock_links: set[tuple[int, int]] = set()
        self.client_arguments: set[ast.expr] = set()
        self.adaptable_arguments: set[ast.expr] = set()
        self.relay_arguments: set[ast.expr] = set()
        self.parameter_links: set[tuple[str, int, str]] = set()
        self.factory_calls: dict[ast.expr, str] = {}
        self.review_arguments: set[ast.expr] = set()
        self.statements: dict[tuple[int, int], int] = {}
        self.calls: list[Call] = []
        self.constructor_envs: dict[tuple[int, int], dict[str, str]] = {}
        self.found = False
        self.statement = 1
        self.binding = ""
        self.source = ""
        self.text = SourceText.parse("")
        self.module = ast.Module(body=[], type_ignores=[])
        self.scope: tuple[str, ...] = ()
        self.in_class_body = False
        self.field_write_arguments: frozenset[ast.AST] = frozenset()
        self.type_bindings: dict[str, str] = {}
        self.deferred: dict[ast.AST, set[str]] = {}
        self.class_enclosing: dict[str, str] = {}
        self.class_lexical: dict[str, str] = {}
        self.class_mutations: set[str] = set()
        self.owner_mutations: set[str] = set()
        self.config_annotations: set[tuple[int, int]] = set()
        self.string_annotation_envs: dict[tuple[int, int], dict[str, str]] = {}
        self.declarations: dict[str, str] = {}
        self.annotation_fallback = {
            key: value
            for key, value in context.index.exports.get(context.module, {}).items()
            if value.startswith(("type:", "annotation:"))
        }

    def loop_probe(self) -> Flow:
        """Share immutable project facts, isolate mutable visitor outputs."""
        probe = copy(self)
        probe.env = self.env.copy()
        probe.type_bindings = self.type_bindings.copy()
        probe.reaching = self.reaching.copy()
        probe.kinds = self.kinds.copy()
        probe.mock_links = self.mock_links.copy()
        probe.client_arguments = self.client_arguments.copy()
        probe.adaptable_arguments = self.adaptable_arguments.copy()
        probe.relay_arguments = self.relay_arguments.copy()
        probe.parameter_links = self.parameter_links.copy()
        probe.factory_calls = self.factory_calls.copy()
        probe.review_arguments = self.review_arguments.copy()
        probe.statements = self.statements.copy()
        probe.calls = []
        probe.constructor_envs = self.constructor_envs.copy()
        probe.class_mutations = self.class_mutations.copy()
        probe.owner_mutations = self.owner_mutations.copy()
        probe.config_annotations = self.config_annotations.copy()
        probe.string_annotation_envs = self.string_annotation_envs.copy()
        probe.declarations = self.declarations.copy()
        probe.class_enclosing = self.class_enclosing.copy()
        probe.class_lexical = self.class_lexical.copy()
        probe.comprehension_writes = tuple(
            writes.copy() for writes in self.comprehension_writes
        )
        return probe

    def annotation_env(self) -> dict[str, str]:
        return {**self.annotation_fallback, **self.env, **self.type_bindings}

    def analyze(self, source: str, module: ast.Module | None = None) -> None:
        self.source = source
        self.text = SourceText.parse(source)
        self.module = module if module is not None else ast.parse(source)
        self.reflective_aliases = reflective_names(self.module)
        self.deferred = DeferredWrites(self.module, self.reflective_aliases).result
        self.class_mutations = class_mutations(self.module, self.reflective_aliases) | {
            name.removeprefix(self.context.module + ".")
            for name in self.context.index.class_mutations
            if name.startswith(self.context.module + ".")
        }
        self.visit(self.module)

    def visit(self, node: ast.AST) -> None:
        mock_specs = MockSpecOrigins(
            self.resolver,
            self.annotation_env()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else self.env,
        ).bindings(node, self.context)
        previous = self.statement
        previous_writes = self.field_write_arguments
        self.field_write_arguments |= precise_write_arguments(node, self)
        if not self.previewing_loop:
            record_annotations(self, node)
        if isinstance(node, ast.stmt):
            self.statement = node.lineno
        record_class_mutations(node, self)
        super().visit(node)
        self.env.update(mock_specs)
        self.field_write_arguments = previous_writes
        if not self.previewing_loop and isinstance(node, ast.expr):
            # An expression's proof reflects its own evaluation, not later siblings.
            self.statements[node.lineno, node.col_offset] = self.statement
            self.kinds[
                node.lineno,
                node.col_offset,
                node.end_lineno or node.lineno,
                node.end_col_offset or node.col_offset,
            ] = MockInference(self.resolver, self.env).review(
                node
            ) or self.resolver.infer(node, self.env)
            if mock_linked(node, self):
                self.mock_links.add((node.lineno, node.col_offset))
            remember_receiver(node, self)
            record_factory_reference(node, self)
        self.statement = previous

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        escape_fields(node.value, self)
        self.bind(node.target, self.resolver.infer(node.value, self.env))
        for writes in self.comprehension_writes:
            name = dotted(node.target)
            kind = self.env[name]
            if name in writes:
                kind = joined_bindings(
                    self.resolver, ({name: writes[name]}, {name: kind})
                ).get(name, "")
            writes[name] = kind
        self.kinds[
            node.lineno,
            node.col_offset,
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        ] = self.resolver.infer(node.value, self.env)

    def visit_Call(self, node: ast.Call) -> None:
        self.visit(node.func)
        record_arguments(node, self)
        is_constructor = not self.previewing_loop and client_constructor(
            self.resolver.infer(node.func, self.env)
        )
        member_writes = member_write_effects(node, self)
        if not self.previewing_loop and isinstance(node.func, ast.Attribute):
            self.calls.append(
                Call(
                    node,
                    receiver_kind(node.func, self),
                    self.text.segment(node.func.value) or "",
                    node.func.attr,
                    self.statement,
                    self.binding,
                    self.scope,
                    node.func.attr in self.dockerfile_methods
                    and passes_dockerfile_image(
                        node,
                        self.resolver.mapping,
                        lambda value: self.resolver.infer(value, self.env),
                    ),
                )
            )
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)
        if is_constructor:
            self.constructor_envs[node.lineno, node.col_offset] = self.env.copy()
        apply_effects(node, self)
        MockInference(self.resolver, self.env).configure(node)
        self.bind(node, "")
        self.env.update((name, "unproven:client") for name in member_writes)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if sys.version_info >= (3, 12):
            for parameter in node.type_params:
                self.visit(parameter)
        for expression in [
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
        ]:
            self.visit(expression)
        kind = constructor_kind(node, self)
        invalidate(self.env, node.name)
        self.env[node.name] = kind
        outer = self.env.copy()
        outer_types = self.type_bindings.copy()
        previous_enclosing = self.class_enclosing
        previous_lexical = self.class_lexical
        if self.in_class_body:
            self.env = previous_lexical.copy()
        self.class_lexical = self.env.copy()
        self.env = class_bindings(node, self, kind)
        class_env = self.env.copy()
        self.class_enclosing = class_env
        previous = self.in_class_body
        previous_pytest_class = self.pytest_class
        self.in_class_body = True
        self.pytest_class = matches_name(
            node.name, self.context.index.collection.classes
        )
        for statement in node.body:
            self.visit(statement)
        self.in_class_body = previous
        self.pytest_class = previous_pytest_class
        self.class_enclosing = previous_enclosing
        self.class_lexical = previous_lexical
        self.type_bindings = outer_types
        self.env = {
            **outer,
            **{k: v for k, v in class_env.items() if k.startswith(node.name + ".")},
        }
