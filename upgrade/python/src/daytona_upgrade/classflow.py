# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Join every class attribute write before using it as an instance proof."""

from __future__ import annotations

import ast

from .attribute_proofs import AttributeProofs
from .bindings import indirect_writes
from .class_imports import ClassImportVisitor
from .class_method_stores import visit_method_stores
from .class_stores import (
    direct_parameter_stores,
    joined_class_stores,
    record_class_value,
    record_client_annotation,
    setter_bindings,
)
from .client_parameters import PARAMETERS
from .constructor_parameters import constructor_parameters
from .controlflow import BindingVisitor
from .descriptors import class_members
from .field_arguments import dataclass_parameters
from .field_factories import factory_kind
from .field_initialization import framework_init
from .field_safety import opaque_fields
from .instance_fields import generated_init
from .proof_mutations import CONTAINER_WRITE, invalidate, mutates_container
from .resolution import Resolver, dotted


class AttributeWrites(ClassImportVisitor):
    """Accumulate actual writes separately from mutable local control-flow state."""

    def __init__(self, resolver: Resolver, enclosing: dict[str, str]) -> None:
        super().__init__()
        self.resolver = resolver
        self.env = enclosing.copy()
        self.writes: dict[str, set[str]] = {}
        self.class_values: set[str] = set()
        self.class_scope = True
        self.type_bindings: dict[str, str] = {}
        self.deferred: dict[ast.AST, set[str]] = {}
        self.in_class_body = False
        self.class_enclosing: dict[str, str] = {}
        self.class_lexical: dict[str, str] = {}
        self.dataclass = False
        self.factory_fields: set[str] = set()
        self.field_framework = ""
        self.generated_init = False
        self.forwarded_fields: set[str] = set()
        self.store_counts: dict[str, int] = {}
        self.definition_envs: dict[ast.AST, dict[str, str]] = {}
        self.client_annotations: set[str] = set()

    def record(self, target: ast.AST, kind: str) -> None:
        proofs = AttributeProofs(self.resolver, self.env, self.reflective_aliases)
        if mutates_container(
            target,
            trusted_reflective=proofs.trusted(target),
        ):
            self.writes.setdefault(CONTAINER_WRITE, set()).add("")
        stores = (
            ((dotted(target), proofs.store_kind(dotted(target), kind)),)
            if dotted(target)
            else proofs.stores(target, kind)
        )
        for name, write_kind in stores:
            root, separator, member = name.partition(".")
            if separator and self.env.get(root) == "instance-receiver":
                key = "self" if member == "*" else "self." + member
                self.store_counts[key] = self.store_counts.get(key, 0) + 1
                self.writes.setdefault(
                    "self" if member == "*" else "self." + member, set()
                ).add(write_kind)
            elif self.class_scope and isinstance(target, ast.Name):
                key = "self." + name
                self.store_counts[key] = self.store_counts.get(key, 0) + 1
                self.writes.setdefault("self." + name, set()).add(kind)
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self.record(element, "")
        elif isinstance(target, ast.Starred):
            self.record(target.value, "")

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        kind = self.resolver.infer(node.value, self.env)
        for target in node.targets:
            record_class_value(target, kind, self)
            AttributeProofs(self.resolver, self.env, self.reflective_aliases).assign(
                ast.Assign(targets=[target], value=node.value), kind
            )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        record_client_annotation(node, self)
        if (
            node.value is None
            and dotted(node.target) in self.env
            and (not self.class_scope or "self." + dotted(node.target) in self.writes)
        ):
            return
        if node.value:
            self.visit(node.value)
        # An annotation never overrules a contradictory concrete write.
        kind = (
            self.resolver.infer(node.value, self.env)
            if node.value
            else self.resolver.annotation(node.annotation, self.annotation_env())
        )
        if self.class_scope and (factory := factory_kind(node, self)) is not None:
            self.factory_fields.add(dotted(node.target))
            kind = factory
        if node.value:
            record_class_value(node.target, kind, self)
        else:
            self.record(node.target, kind)
        self.bind(node.target, kind)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        kind = self.resolver.infer(node.value, self.env)
        record_class_value(node.target, kind, self)
        self.bind(node.target, kind)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.record(node, "")
            self.bind(node, "")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.record(node, "")
            self.bind(node, "")

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if indirect_writes(node, self.reflective_aliases) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            self.record(node, "")
            self.bind(node, "")
            self.generic_visit(node)
            return
        root = dotted(node.value).split(".", 1)[0]
        if (
            isinstance(node.ctx, (ast.Store, ast.Del))
            and self.env.get(root) == "instance-receiver"
        ):
            # __setitem__ or an instance __dict__ write can replace any field.
            self.writes.setdefault("self", set()).add("")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        self.record(node, "")
        self.bind(node, "")

    def comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        class_scope = self.class_scope
        # Target names live in the comprehension, not in the surrounding class.
        self.class_scope = False
        super().comprehension(node)
        self.class_scope = class_scope

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.record(target, "")
        super().visit_Delete(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.record(node, "")
        self.record(node.target, "")
        super().visit_AugAssign(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # A closure's stores do not establish instance initialization facts.
        self.definition_envs[node] = self.env.copy()
        invalidate(self.env, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        invalidate(self.env, node.name)


def class_bindings(
    node: ast.ClassDef, enclosing: BindingVisitor, constructor: str
) -> dict[str, str]:
    resolver = enclosing.resolver
    bindings = {
        key: kind for key, kind in enclosing.env.items() if not key.startswith("self.")
    }
    bindings["self"] = (
        constructor.replace("type:client-subclass:", "self-subclass:", 1)
        if constructor.startswith("type:client-subclass:")
        else "instance:" + node.name
    )
    bindings.update(
        setter_bindings(
            node,
            enclosing.env,
            tuple(resolver.infer(base, enclosing.env) for base in node.bases),
        )
    )
    writes = AttributeWrites(resolver, bindings)
    writes.forwarded_fields = direct_parameter_stores(node, enclosing)
    writes.reflective_aliases = enclosing.reflective_aliases
    writes.class_mutations = enclosing.class_mutations
    writes.dataclass = any(
        resolver.infer(
            decorator.func if isinstance(decorator, ast.Call) else decorator,
            enclosing.env,
        )
        == "call:dataclasses.dataclass"
        for decorator in node.decorator_list
    )
    if writes.dataclass:
        bindings.update(dataclass_parameters(node, enclosing))
        if generated_init(node, enclosing):
            writes.generated_init = True
            writes.env["@default-init"] = "dataclass"
    else:
        writes.field_framework = framework_init(node, enclosing)
        writes.env["@default-init"] = writes.field_framework
    if opaque_fields(constructor):
        bindings[node.name + ".@unsafe-fields"] = "opaque"
    writes.type_bindings = enclosing.type_bindings.copy()
    writes.deferred = enclosing.deferred
    writes.in_class_body = True
    writes.class_enclosing = bindings.copy()
    writes.class_lexical = enclosing.env.copy()
    for statement in node.body:
        writes.visit(statement)
    writes.class_scope = writes.in_class_body = False
    bindings.update(class_members(node, writes, enclosing.annotation_env()))
    for statement in node.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes.env = bindings.copy()
        visit_method_stores(statement, writes, enclosing.annotation_env())
    bindings[node.name + PARAMETERS] = constructor_parameters(node, writes)
    return joined_class_stores(node.name, bindings, writes)
