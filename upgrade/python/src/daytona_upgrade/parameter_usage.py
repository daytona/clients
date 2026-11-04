# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Certify the consumer's namespace transition before reshaping its caller's fake."""

from __future__ import annotations

import ast

from .annotation_proofs import AnnotationProofs
from .collection_proofs import tuple_members
from .double_ownership import mutates_namespace


def parameter_usage(
    node: ast.FunctionDef | ast.AsyncFunctionDef, annotations: AnnotationProofs
) -> dict[str, str]:
    """Encode changed-safe, changed-review, and safe typed-return relay slots."""
    from .client_parameters import BOUND_PARAMETERS, PARAMETERS, instance_receiver
    from .flow import SourceContext
    from .generator_proofs import FunctionBindings
    from .parameter_proofs import parameter_bindings
    from .parameter_relay_flow import ParameterFlow
    from .parameter_relay_uses import RelayUses

    parameters = parameter_bindings(
        node, annotations, FunctionBindings(annotations.env, annotations.env)
    )
    receiver = instance_receiver(node, annotations)
    if receiver and annotations.env.get(receiver, "").startswith("instance:"):
        parameters[receiver] = annotations.env[receiver]
    clients = {
        name
        for name, kind in parameters.items()
        if name.isidentifier() and kind == "client"
    }
    if not clients:
        return {}
    flow = ParameterFlow(
        annotations.resolver, SourceContext("", "", annotations.resolver.index)
    )
    flow.env.update(
        (name, kind)
        for name, kind in annotations.resolver.index.exports.get(
            annotations.env.get("@module", ""), {}
        ).items()
        if name.endswith((PARAMETERS, BOUND_PARAMETERS)) or kind.startswith("fn:")
    )
    flow.env.update({**annotations.env, **parameters})
    flow.declarations = {
        name: kind
        for name, kind in parameters.items()
        if kind in {"client", "unproven:client"}
    }
    reaches = flow.visit_suite(node.body)
    parents = {
        child: parent
        for statement in node.body
        for parent in ast.walk(statement)
        for child in ast.iter_child_nodes(parent)
    }
    changed: set[str] = set()
    relayed: set[str] = set()
    paths: dict[str, set[str]] = {}
    result = annotations.resolve(node.returns)
    relay_uses = RelayUses(flow, parents, result, relays=flow.relays)
    unsafe: set[str] = clients.copy() if mutates_namespace(node) else set()
    for reference, parent in parents.items():
        if not isinstance(reference, ast.Name) or reference.id not in clients:
            continue
        if isinstance(reference.ctx, ast.Store):
            continue
        if reference in flow.relays:
            call, path = flow.relays[reference]
            use = relay_uses.inspect(call, path)
            if use.changed:
                changed.add(reference.id)
            if not use.safe:
                unsafe.add(reference.id)
            if use.returned:
                relayed.add(reference.id)
                paths.setdefault(reference.id, set()).add(use.returned)
            continue
        if (
            reference in flow.client_arguments
            and reference not in flow.adaptable_arguments
        ):
            changed.add(reference.id)
            unsafe.add(reference.id)
            continue
        if reference in flow.relay_arguments:
            (changed if reference in flow.adaptable_arguments else relayed).add(
                reference.id
            )
            continue
        if (
            isinstance(parent, ast.Compare)
            and len(parent.ops) == 1
            and isinstance(parent.ops[0], (ast.Is, ast.IsNot))
            and any(
                isinstance(value, ast.Constant) and value.value is None
                for value in [parent.left, *parent.comparators]
            )
        ):
            continue
        if (
            isinstance(parent, ast.Return)
            and parent.value is reference
            and result == "client"
        ):
            relayed.add(reference.id)
            paths.setdefault(reference.id, set()).add("return")
            continue
        if isinstance(parent, ast.Tuple) and isinstance(
            parents.get(parent), ast.Return
        ):
            members = tuple_members(result)
            if len(members) == len(parent.elts) and members[
                parent.elts.index(reference)
            ] in {"client", "unproven:client"}:
                relayed.add(reference.id)
                guards = ":".join(
                    f"{index}={int(value.value)}"
                    for index, value in enumerate(parent.elts)
                    if isinstance(value, ast.Constant) and type(value.value) is bool
                )
                path = str(parent.elts.index(reference)) + (
                    ":" + guards if guards else ""
                )
                paths.setdefault(reference.id, set()).add(path)
                continue
        kind = flow.kinds.get(
            (
                reference.lineno,
                reference.col_offset,
                reference.end_lineno or reference.lineno,
                reference.end_col_offset or reference.col_offset,
            ),
            "",
        )
        if (
            isinstance(parent, ast.Attribute)
            and parent.value is reference
            and kind == "client"
            and (isinstance(parent.ctx, ast.Load) or parent.attr == "default_language")
        ):
            if parent.attr in annotations.resolver.mapping.moved or parent.attr == "v0":
                changed.add(reference.id)
                continue
        if (
            isinstance(parent, ast.Call)
            and parent.args
            and parent.args[0] is reference
            and kind == "client"
        ):
            function = flow.kinds.get(
                (
                    parent.func.lineno,
                    parent.func.col_offset,
                    parent.func.end_lineno or parent.func.lineno,
                    parent.func.end_col_offset or parent.func.col_offset,
                ),
                "",
            )
            if (
                function in {"call:builtins.getattr", "call:builtins.hasattr"}
                and function.removeprefix("call:builtins.")
                not in annotations.resolver.index.builtin_writes
                and len(parent.args)
                in ({2, 3} if function.endswith("getattr") else {2})
                and not parent.keywords
                and isinstance(parent.args[1], ast.Constant)
                and parent.args[1].value in annotations.resolver.mapping.moved
            ):
                changed.add(reference.id)
                continue
        unsafe.add(reference.id)
    # Every use must preserve identity at one return slot. Other branches may
    # return fresh clients; literal boolean siblings discriminate their cleanup.
    result_client = result in {
        "client",
        "items:client",
        "items:unproven:client",
    } or any(kind in {"client", "unproven:client"} for kind in tuple_members(result))
    candidates = relayed | (unsafe if result_client else set())
    stored = {
        child.id
        for child in parents
        if isinstance(child, ast.Name) and not isinstance(child.ctx, ast.Load)
    }
    return {
        **{
            name: "~"
            + (
                next(iter(paths[name]))
                if not reaches
                and name not in unsafe | stored
                and len(paths.get(name, set())) == 1
                else "?"
            )
            + "/"
            for name in candidates - changed
        },
        **{name: "!" if name in unsafe else "" for name in changed},
    }
