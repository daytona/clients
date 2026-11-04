# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class-wide store joins preserve linked evidence and setter uncertainty."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .attribute_proofs import _review_only
from .field_effects import CLIENT_ANNOTATION
from .mock_spec_origins import SYNC_MARKER
from .proof_mutations import CONTAINER_WRITE
from .resolution import dotted

if TYPE_CHECKING:
    from .classflow import AttributeWrites
    from .controlflow import BindingVisitor


def property_setter(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A setter declaration must not overwrite the getter's lookup evidence."""
    return any(
        isinstance(decorator, ast.Attribute) and decorator.attr == "setter"
        for decorator in node.decorator_list
    )


def setter_bindings(
    node: ast.ClassDef, enclosing: dict[str, str], bases: tuple[str, ...]
) -> dict[str, str]:
    """Record project-defined setter destinations before collecting method stores."""
    inherited = {
        prefix + ".@setter." + key.partition(".@setter.")[2]: "setter"
        for base in bases
        if base.startswith("class:")
        for key in enclosing
        if key.startswith(base.split(":")[1] + ".@setter.")
        for prefix in ("self", node.name)
    }
    return {
        **inherited,
        **{
            prefix
            + ".@setter."
            + (
                decorator.value.id
                if isinstance(decorator.value, ast.Name)
                else decorator.value.attr
            ): "setter"
            for statement in node.body
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in statement.decorator_list
            if isinstance(decorator, ast.Attribute)
            and decorator.attr == "setter"
            and isinstance(decorator.value, (ast.Name, ast.Attribute))
            for prefix in ("self", node.name)
        },
    }


def joined_class_stores(
    owner: str, bindings: dict[str, str], writes: AttributeWrites
) -> dict[str, str]:
    """Join concrete stores without promoting conflicting or wildcard writes."""
    wildcard = writes.writes.get("self", set())
    # Bare member fences are needed only when the mutation's owner is unresolved.
    sync_mutations = {name for name in writes.class_mutations if "." in name}
    members = {name.rpartition(".")[2] for name in sync_mutations}
    sync_mutations.update(writes.class_mutations - members)
    for name in tuple(sync_mutations):
        target, separator, member = name.rpartition(".")
        if not separator or target == "self":
            continue
        kind = writes.resolver.infer(
            ast.parse(target, mode="eval").body, writes.class_enclosing
        )
        if kind.startswith(("class:", "instance:")):
            sync_mutations.add(kind.split(":")[1] + "." + member)
        else:
            sync_mutations.add(member)
    for name in list(bindings):
        if name.startswith(owner + ".") and name.endswith(SYNC_MARKER):
            member = name.removeprefix(owner + ".").removesuffix(SYNC_MARKER)
            if (
                wildcard
                or "self." + member in writes.writes
                or owner + "." + member in sync_mutations
                or member in sync_mutations
            ):
                bindings.pop(name)
    if any(_review_only(kind) for kind in wildcard):
        evidence = {_review_only(kind) for kind in wildcard} - {""}
        kind = (
            "items:unproven:client"
            if evidence == {"items:unproven:client"}
            else "unproven:client"
        )
        bindings["self.*"] = bindings[owner + ".*"] = kind
    for name, kinds in writes.writes.items():
        if name in {"self", CONTAINER_WRITE}:
            continue
        member = name.removeprefix("self.")
        class_name = owner + "." + member
        kind = next(iter(kinds)) if len(kinds) == 1 else ""
        if (
            kind.startswith("items:")
            and CONTAINER_WRITE in writes.writes
            or wildcard
            or class_name in writes.class_mutations
            or member in writes.class_mutations
        ):
            kind = ""
        evidence = [
            *kinds,
            bindings.get(name, ""),
            bindings.get(class_name, "").removeprefix("property:"),
            "client" if member in writes.client_annotations else "",
        ]
        if not kind and any(_review_only(value) for value in evidence):
            kind = "unproven:client"
        if bindings.get(class_name, "").startswith("property:"):
            descriptor = bindings[class_name].removeprefix("property:")
            if descriptor != kind and any(
                _review_only(value) for value in [descriptor, *kinds]
            ):
                kind = "unproven:client"
            bindings[class_name] = "property:" + kind
        if owner + ".@parameter." + member in bindings:
            bindings[owner + ".@argument-safe." + member] = (
                "safe"
                if not wildcard
                and class_name not in writes.class_mutations
                and member not in writes.class_mutations
                and owner + ".@setter." + member not in bindings
                and not bindings.get(class_name, "").startswith("property:")
                and (
                    writes.generated_init
                    and len(kinds) == 1
                    and kind == next(iter(kinds))
                    or member in writes.forwarded_fields
                    # One declaration plus exactly one direct initializer store.
                    and writes.store_counts[name] == 2
                )
                else ""
            )
        bindings[name] = kind
        if member in writes.factory_fields:
            bindings[owner + ".@factory." + member] = kind
        if name in writes.class_values and member not in writes.factory_fields:
            bindings[class_name] = kind
        elif class_name not in bindings and _review_only(kind):
            bindings[class_name] = "unproven:client"
    bindings.update(
        (root + CLIENT_ANNOTATION + member, "client")
        for member in writes.client_annotations
        for root in ("self", owner)
    )
    return bindings


def record_client_annotation(node: ast.AnnAssign, writes: AttributeWrites) -> None:
    """Retain explicit field contracts separately from joined concrete stores."""
    if writes.resolver.annotation(node.annotation, writes.annotation_env()) != "client":
        return
    name = dotted(node.target)
    root, _, member = name.partition(".")
    if writes.class_scope and isinstance(node.target, ast.Name):
        writes.client_annotations.add(name)
    elif writes.env.get(root) == "instance-receiver" and member:
        writes.client_annotations.add(member)


def record_class_value(target: ast.AST, kind: str, writes: AttributeWrites) -> None:
    """Distinguish class-body values from instance-only method stores."""
    writes.record(target, kind)
    if writes.class_scope and isinstance(target, ast.Name):
        writes.class_values.add("self." + target.id)


def direct_parameter_stores(node: ast.ClassDef, enclosing: BindingVisitor) -> set[str]:
    """Prove only straight-line, same-name parameter stores in an ordinary init."""
    if (
        node.bases
        or node.keywords
        or not all(
            enclosing.resolver.infer(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                enclosing.env,
            )
            == "call:dataclasses.dataclass"
            for decorator in node.decorator_list
        )
        or any(
            isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
            and statement.name == "__new__"
            for statement in node.body
        )
    ):
        return set()
    initializers = [
        statement
        for statement in node.body
        if isinstance(statement, ast.FunctionDef) and statement.name == "__init__"
    ]
    if len(initializers) != 1 or initializers[0].decorator_list:
        return set()
    initializer = initializers[0]
    parameters = {
        arg.arg for arg in [*initializer.args.args, *initializer.args.kwonlyargs]
    }
    fields: set[str] = set()
    for statement in initializer.body:
        # Other statements could rebind a parameter, bypass a store, or transform it.
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Attribute)
            and isinstance(statement.targets[0].value, ast.Name)
            and statement.targets[0].value.id == "self"
            and isinstance(statement.value, ast.Name)
            and statement.value.id == statement.targets[0].attr
            and statement.value.id in parameters - {"self"}
        ):
            return set()
        fields.add(statement.value.id)
    return fields
