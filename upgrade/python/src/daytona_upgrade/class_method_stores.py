# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Observe method stores with definition-time annotations and isolated locals."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .annotation_proofs import AnnotationProofs
from .client_parameters import instance_receiver
from .fallback_proofs import required_client_annotation
from .proof_mutations import invalidate

if TYPE_CHECKING:
    from .classflow import AttributeWrites


def visit_method_stores(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    writes: AttributeWrites,
    annotations: dict[str, str],
) -> None:
    """A method's locals cannot inherit receiver proofs from another method."""
    for name in writes.assigned_names(node):
        if name.isidentifier():
            invalidate(writes.env, name)
    receiver = instance_receiver(
        node, AnnotationProofs(writes.resolver, writes.definition_envs[node])
    )
    for name in writes.deferred.get(node, set()):
        invalidate(writes.env, name)
    if receiver == "self":
        writes.env["self"] = "instance-receiver"
    else:
        invalidate(writes.env, "self")
    for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        if arg.arg != receiver or arg.arg != "self":
            invalidate(writes.env, arg.arg)
            writes.env[arg.arg] = writes.resolver.annotation(
                arg.annotation, annotations
            )
            if required_client_annotation(arg.annotation, writes.resolver, annotations):
                writes.env[arg.arg + ".@nonnull"] = "client"
    for arg in [node.args.vararg, node.args.kwarg]:
        if arg:
            invalidate(writes.env, arg.arg)
            if arg is node.args.vararg:
                kind = AnnotationProofs(writes.resolver, annotations).element(
                    arg.annotation
                )
                writes.env[arg.arg] = "items:" + kind if kind else ""
    outer_types = writes.type_bindings.copy()
    writes.visit_suite(node.body)
    writes.type_bindings = outer_types
