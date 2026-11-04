# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Freeze lazy aliases at the defining module's import boundary."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .annotation_proofs import AnnotationProofs
from .instance_fields import exported_classes, exported_instance
from .module_proofs import REVIEW_MODULE_CLIENT, module_review

if TYPE_CHECKING:
    from .flow import Flow
    from .resolution import Resolver


def annotation_alias(node: ast.AST, proofs: AnnotationProofs) -> str:
    """Snapshot eager type evidence; keep strings lazy for forward alias references."""
    value = proofs.resolver.infer(node, proofs.env)
    if value.startswith(("type:", "annotation:", "alias:")):
        return value
    kind = proofs.resolve(node)
    if kind and "unproven:" not in kind:
        return "annotation:" + kind
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        if kind:
            return "annotation:" + kind
        if proofs.mentions_client(node):
            return "annotation:unproven:client"
        return ""
    return "alias:" + ast.unparse(node)


def resolved_exports(flow: Flow) -> dict[str, str]:
    """Export proof kinds, never syntax that a consumer could reinterpret."""
    bindings = {**flow.env, **flow.type_bindings}
    proofs = AnnotationProofs(flow.resolver, bindings)
    exports = {
        name: (
            "annotation:" + (proofs.resolve(ast.Name(id=name)) or "non-client")
            if (kind.startswith("alias:") or name in flow.type_bindings)
            and not kind.startswith(("module:", "local-module:"))
            else exported_instance(kind, bindings, flow.context.module)
        )
        for name, kind in bindings.items()
    }
    return exported_classes(exports, flow.context.module)


def conflicting_bindings(
    resolver: Resolver,
    branches: tuple[dict[str, str], dict[str, str]],
    names: Iterable[str] | None = None,
) -> dict[str, str]:
    """A branch-specific client type is review evidence, not a lost binding."""
    left, right = branches
    modules: dict[str, str] = {}
    for name in left.keys() | right.keys() if names is None else names:
        if left.get(name) == right.get(name):
            continue
        reviews = tuple(
            review
            for branch in branches
            if (review := module_review(branch.get(name, "")))
        )
        if reviews:
            modules[name] = (
                ("fn:" if all(kind.startswith("fn:") for kind in reviews) else "")
                + REVIEW_MODULE_CLIENT
                if any(REVIEW_MODULE_CLIENT in kind for kind in reviews)
                else reviews[0]
            )
    clients = {
        name: (
            "fn:items:unproven:client"
            if any(
                branch.get(name) in {"fn:items:client", "fn:items:unproven:client"}
                for branch in branches
            )
            else "fn:unproven:client"
            if any(
                branch.get(name) in {"fn:client", "fn:unproven:client"}
                for branch in branches
            )
            else "unproven:client"
            if any(
                branch.get(name) in {"client", "unproven:client"} for branch in branches
            )
            else "client-sequence"
            if any(branch.get(name) == "client-sequence" for branch in branches)
            else "annotation:unproven:client"
        )
        for name in (left.keys() | right.keys() if names is None else names)
        if left.get(name) != right.get(name)
        and any(
            branch.get(name)
            in {
                "client",
                "unproven:client",
                "client-sequence",
                "fn:client",
                "fn:unproven:client",
                "fn:items:client",
                "fn:items:unproven:client",
            }
            or branch.get(name, "").startswith(("type:", "annotation:", "alias:"))
            and AnnotationProofs(resolver, branch).mentions_client(ast.Name(id=name))
            for branch in branches
        )
    }
    return {
        **modules,
        **clients,
        **{
            name: (
                "fn:"
                if modules[name].startswith("fn:") and kind.startswith("fn:")
                else ""
            )
            + REVIEW_MODULE_CLIENT
            for name, kind in clients.items()
            if name in modules
        },
    }


def joined_bindings(
    resolver: Resolver, branches: tuple[dict[str, str] | None, dict[str, str] | None]
) -> dict[str, str]:
    """Merge only paths that reach the join; None denotes an abrupt exit."""
    left, right = branches
    if left is None:
        return right if right is not None else {}
    if right is None:
        return left
    if left == right:
        # Prefix accumulators mutate the result; never return their input alias.
        return left.copy()
    return {
        **{name: kind for name, kind in left.items() if kind == right.get(name)},
        **conflicting_bindings(resolver, (left, right)),
    }
