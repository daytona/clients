# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Holder escapes fence field proofs without changing direct client identity."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from .resolution import Resolver

CAPTURES: Final = ".@captures"
CLIENT_ANNOTATION: Final = ".@client-annotation."


class FieldContext(Protocol):
    @property
    def env(self) -> dict[str, str]: ...

    @property
    def reflective_aliases(self) -> dict[str, str]: ...


class CallContext(FieldContext, Protocol):
    @property
    def resolver(self) -> Resolver: ...

    @property
    def field_write_arguments(self) -> frozenset[ast.AST]: ...


def precise_write_arguments(node: ast.AST, flow: CallContext) -> frozenset[ast.AST]:
    """Recognized literal field stores expose only their replacement values."""
    from .attribute_proofs import AttributeProofs
    from .bindings import indirect_assignments

    if not isinstance(node, (ast.Call, ast.Subscript)):
        return frozenset()
    if isinstance(node, ast.Subscript) and not isinstance(
        node.ctx, (ast.Store, ast.Del)
    ):
        return frozenset()
    writes = indirect_assignments(node, flow.reflective_aliases, flow.env)
    if not writes or any(name.endswith(".*") for name, _ in writes):
        return frozenset()
    if not AttributeProofs(flow.resolver, flow.env, flow.reflective_aliases).trusted(
        node
    ):
        return frozenset()
    # AST dispatch uses isinstance to retain the tool's Python 3.9 syntax floor.
    if isinstance(node, ast.Subscript):
        receiver = node.value
    elif isinstance(node.func, ast.Attribute) and node.func.attr == "update":
        receiver = node.func.value
    else:
        return frozenset(node.args[:1]) if not node.keywords else frozenset()
    if isinstance(receiver, ast.Call) and not receiver.keywords:
        return frozenset(receiver.args[:1])
    return frozenset()


def captures(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> str:
    """Record lexical references, never certify a function's effects as safe."""
    from ._scopes import ScopeWrites

    parameters = {
        arg.arg
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    }
    parameters.update(arg.arg for arg in [node.args.vararg, node.args.kwarg] if arg)
    body = (
        node.body
        if isinstance(node, ast.Lambda)
        else ast.Module(body=node.body, type_ignores=[])
    )
    writes = ScopeWrites()
    writes.visit(body)
    locals_ = {name for name, _ in writes.events if name.isidentifier()} - writes.shared
    return ",".join(
        sorted(
            {child.id for child in ast.walk(body) if isinstance(child, ast.Name)}
            - parameters
            - locals_
        )
    )


def argument_roots(node: ast.AST, bound: frozenset[str] = frozenset()) -> set[str]:
    """Traverse argument carriers, including dictionary keys and nested expressions."""
    from .resolution import dotted

    if name := dotted(node):
        return {name} if name.split(".")[0] not in bound else set()
    if isinstance(node, ast.Call):
        # Nested calls apply their own boundary; their result is a new value.
        return set()
    if isinstance(node, ast.Lambda):
        return set(filter(None, captures(node).split(","))) - bound
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        # The first iterable runs outside the comprehension's local scope.
        roots = argument_roots(node.generators[0].iter, bound)
        local = bound | frozenset(
            child.id
            for generator in node.generators
            for child in ast.walk(generator.target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        )
        for generator in node.generators:
            if generator is not node.generators[0]:
                roots.update(argument_roots(generator.iter, local))
            for condition in generator.ifs:
                roots.update(argument_roots(condition, local))
        values = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        for value in values:
            roots.update(argument_roots(value, local))
        return roots
    return {
        root
        for child in ast.iter_child_nodes(node)
        for root in argument_roots(child, bound)
    }


def fence_fields(root: str, flow: FieldContext) -> None:
    """Demote descendant client evidence; passing a client never rebinds it."""
    from .attribute_proofs import _review_only

    roots = {root, flow.env.get(root + ".@owner", root)}
    roots.update(
        owner
        for key, owner in flow.reflective_aliases.items()
        if key.startswith("@owner:" + root + ":")
    )
    roots.update(
        key.removesuffix(".@owner")
        for key, owner in flow.env.items()
        if key.endswith(".@owner") and owner in roots
    )
    fields: dict[str, str] = {}
    for owner in roots:
        fields.update(
            (key, _review_only(value))
            for key, value in flow.env.items()
            if key.startswith(owner + ".")
            and ".@" not in key
            and _review_only(value)
            and flow.env.get(owner + CLIENT_ANNOTATION + key.removeprefix(owner + "."))
            != "client"
        )
        kind = flow.env.get(owner, "")
        if kind.startswith("instance:"):
            class_name = kind.split(":")[1]
            fields.update(
                (
                    owner + key.removeprefix(class_name),
                    _review_only(value.removeprefix("property:")),
                )
                for key, value in flow.env.items()
                if key.startswith(class_name + ".")
                and ".@" not in key
                and _review_only(value.removeprefix("property:"))
                and flow.env.get(
                    class_name + CLIENT_ANNOTATION + key.removeprefix(class_name + ".")
                )
                != "client"
            )
    flow.env.update(fields)


def escape_fields(node: ast.AST, flow: FieldContext) -> None:
    """Fence every holder reachable through an escape, including lexical captures."""
    pending = list(argument_roots(node))
    seen: set[str] = set()
    while pending:
        root = pending.pop()
        if root in seen:
            continue
        seen.add(root)
        pending.extend(filter(None, flow.env.get(root + CAPTURES, "").split(",")))
        fence_fields(root, flow)


def apply_effects(node: ast.Call, flow: CallContext) -> None:
    """Fence opaque calls; literal reflective writes retain sibling-field proofs."""
    kind = flow.resolver.infer(node.func, flow.env)
    # Dependency registration stores the factory without calling it or mutating
    # its captured class fields. Nested argument expressions have their own visit.
    if kind == "module:fastapi.Depends":
        return
    # Calling a known class constructs an instance; it does not pass that class
    # as a holder argument. Passing the class explicitly still fences its fields.
    constructor = kind.startswith("class:")
    if not constructor:
        escape_fields(node.func, flow)
    for expression in [*node.args, *(kw.value for kw in node.keywords)]:
        if expression not in flow.field_write_arguments:
            escape_fields(expression, flow)
    if (
        not constructor
        and isinstance(node.func, ast.Attribute)
        and not (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and flow.env.get("self", "").startswith(("instance:", "self-subclass:"))
        )
    ):
        escape_fields(node.func.value, flow)
