# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Function-definition evaluation and deferred body scopes."""

from __future__ import annotations

import ast
import sys
from pathlib import PurePath
from typing import TYPE_CHECKING

from .annotation_exports import joined_bindings
from .annotation_proofs import AnnotationProofs
from .call_sites import CALL_PARAMETERS, call_parameters
from .client_parameters import (
    PARAMETER_OWNER,
    PARAMETERS,
    instance_receiver,
    parameter_contract,
    parameter_owner,
)
from .compoundflow import CompoundVisitor
from .field_effects import CAPTURES, captures
from .generator_proofs import function_bindings
from .parameter_proofs import fixture_bindings, parameter_bindings
from .proof_mutations import CONTAINER_WRITE, invalidate
from .pytest_collection import matches_name

if TYPE_CHECKING:
    from .flow import SourceContext


class FunctionVisitor(CompoundVisitor):
    """Enter function bodies without leaking local proof state to their caller."""

    scope: tuple[str, ...]
    in_class_body: bool
    pytest_class: bool = False
    context: SourceContext
    declarations: dict[str, str]

    def visit(self, node: ast.AST) -> None:
        super().visit(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Restore lexical locals, but never revive a shared binding that a
            # nested writer can change before subsequent uses in its owner.
            for child in (
                child
                for child in ast.walk(node)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ):
                writes = self.scope_writes(child)
                for name, _ in writes.events:
                    if name == CONTAINER_WRITE or name.split(".")[0] in writes.shared:
                        self.fence(name)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for expression in [
            *node.decorator_list,
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value),
        ]:
            self.visit(expression)
        # PEP 695 type params (absent before Python 3.12) carry type syntax too.
        if sys.version_info >= (3, 12):
            for parameter in node.type_params:
                self.visit(parameter)
        if node.returns:
            self.visit(node.returns)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if arg.annotation:
                self.visit(arg.annotation)
        annotations = self.annotation_env()
        runtime = function_bindings(node, self.env, self.deferred)
        kind = self.resolver.function_kind(node, annotations, runtime)
        if not kind:
            kind = AnnotationProofs(self.resolver, annotations).element(node.returns)
        invalidate(self.env, node.name)
        self.env[node.name] = "fn:" + kind if kind else ""
        self.env[node.name + CAPTURES] = captures(node)
        self.env[node.name + CALL_PARAMETERS] = call_parameters(
            node, self.context.module
        )
        self.env[node.name + PARAMETERS] = parameter_contract(
            node, AnnotationProofs(self.resolver, annotations)
        )
        is_fixture = any(
            self.resolver.infer(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                runtime.definition,
            )
            == "module:pytest.fixture"
            for decorator in node.decorator_list
        )
        if is_fixture:
            # Empty declarations still shadow ancestor fixtures of the same name.
            self.env["@fixture." + node.name] = (
                kind.removeprefix("items:")
                if kind.removeprefix("items:").startswith(("mock:", "mock-class:"))
                else "unproven:client"
                if kind
                in {
                    "client",
                    "unproven:client",
                    "items:client",
                    "items:unproven:client",
                }
                else ""
            )
        outer = self.env.copy()
        if self.in_class_body:
            self.env = self.class_enclosing.copy()
        outer_types = self.type_bindings.copy()
        for name in self.deferred.get(node, set()):
            self.fence(name)
        if "*" in self.deferred.get(node, set()):
            annotations = self.annotation_env()
        parameters = parameter_bindings(
            node, AnnotationProofs(self.resolver, annotations), runtime
        )
        outer_declarations = self.declarations
        self.declarations = {
            arg.arg: kind
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if (kind := self.resolver.annotation(arg.annotation, annotations))
            in {"client", "unproven:client"}
        }
        scope = self.scope
        method = self.in_class_body
        receiver = instance_receiver(
            node, AnnotationProofs(self.resolver, runtime.definition)
        )
        if method and receiver != "self":
            invalidate(self.env, "self")
        self.in_class_body = False
        self.scope = (*scope, node.name)
        # Python locals shadow outer bindings throughout a function, including before assignment.
        for name in self.assigned_names(node):
            if "." not in name:
                invalidate(self.env, name)
        for name, value in self.context.index.exports.get(
            self.context.module, {}
        ).items():
            if name.startswith("@global.") and name[8:] not in self.assigned_names(
                node
            ):
                self.env = joined_bindings(
                    self.resolver, (self.env, {**self.env, name[8:]: value})
                )
        # Fixture names are evidence only where pytest can inject parameters:
        # collected tests in collected modules, fixtures, and conftest hooks.
        injected = (
            self.context.index.collection.module(self.context.file)
            and matches_name(node.name, self.context.index.collection.functions)
            and (not method or self.pytest_class)
            or is_fixture
            or PurePath(self.context.file).name == "conftest.py"
        )
        fixtures = fixture_bindings(node, self.context) if injected else {}
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            # The implicit instance keeps class-wide proofs unless locally rebound.
            if not (method and arg.arg == receiver == "self"):
                invalidate(self.env, arg.arg)
                kind = parameters[arg.arg]
                if (
                    not kind
                    and arg.annotation is None
                    and (self.context.module, node.lineno, arg.arg)
                    in self.context.index.parameter_links
                ):
                    kind = "unproven:client"
                if (
                    injected
                    and arg.arg in {"mocker", "monkeypatch"}
                    and arg.arg not in fixtures
                    and not kind
                ):
                    kind = (
                        "module:pytest_mock.MockerFixture"
                        if arg.arg == "mocker"
                        else "module:pytest.MonkeyPatch"
                    )
                # Injection adds review evidence; independent client annotations
                # keep the rewrite proof they already established.
                if fixtures.get(arg.arg) and kind != "client":
                    fixture_kind = fixtures[arg.arg]
                    kind = (
                        (
                            fixture_kind
                            if not kind or kind == fixture_kind
                            else "mock:uncertain"
                        )
                        if fixture_kind.startswith(("mock:", "mock-class:"))
                        else joined_bindings(
                            self.resolver, ({arg.arg: kind}, {arg.arg: fixture_kind})
                        ).get(arg.arg, "")
                    )
                    if kind in {"mock:async-client", "mock:uncertain"}:
                        kind = "fixture:" + kind
                self.env[arg.arg] = kind
                if (
                    parameters.get(arg.arg + ".@nonnull") == "client"
                    and kind == "client"
                ):
                    self.env[arg.arg + ".@nonnull"] = "client"
                self.env[arg.arg + PARAMETER_OWNER] = (
                    kind
                    if kind.startswith("instance:")
                    else parameter_owner(
                        arg.annotation, AnnotationProofs(self.resolver, annotations)
                    )
                )
        for arg in [node.args.vararg, node.args.kwarg]:
            if arg:
                invalidate(self.env, arg.arg)
                if arg is node.args.vararg:
                    kind = AnnotationProofs(self.resolver, annotations).element(
                        arg.annotation
                    )
                    self.env[arg.arg] = "items:" + kind if kind else ""
        prefixes: dict[str, str] = {}
        self.prefix_proofs = (*self.prefix_proofs, prefixes)
        self.visit_suite(node.body)
        self.prefix_proofs = self.prefix_proofs[:-1]
        # A direct literal return has inspectable URL fields, unlike a return
        # annotation. Use the body bindings after parameter/deferred-write fences.
        if (
            isinstance(node, ast.FunctionDef)
            and not node.decorator_list
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Return)
            and isinstance(node.body[0].value, ast.Call)
            and self.resolver.infer(node.body[0].value.func, self.env) == "type:config"
        ):
            outer[node.name] = "fn:literal-config"
        writes = self.scope_writes(node)
        shared = writes.shared & {name.split(".")[0] for name, _ in writes.events}
        globals_ = {
            name
            for child in ast.walk(node)
            if isinstance(child, ast.Global)
            for name in child.names
        }
        for name in shared:
            if prefixes.get(name) in {"client", "unproven:client"}:
                outer = joined_bindings(
                    self.resolver, (outer, {**outer, name: "unproven:client"})
                )
                if name in globals_:
                    outer["@global." + name] = "unproven:client"
        outer.update(
            (node.name + "." + name, "fn:unproven:client")
            for name in prefixes
            if name.startswith("@member-write.") and name not in outer
        )
        self.env = outer
        self.declarations = outer_declarations
        self.type_bindings = outer_types
        self.scope = scope
        self.in_class_body = method

    visit_AsyncFunctionDef = visit_FunctionDef
