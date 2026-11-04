# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Replacing SDK-owned moved members cannot certify later method dispatch."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import libcst as cst

from .imports import module_name
from .models import Mapping
from .subclasses import client_constructor, member_receiver

if TYPE_CHECKING:
    from .attribute_proofs import AttributeProofs
    from .attributes import AttributeContext
    from .flow import Flow


def replacement(member: str, stored: str, mapping: Mapping) -> bool:
    """A setting or a proven same-service assignment is not method replacement."""
    return (
        member in mapping.moved
        and member != "default_language"
        and not any(
            stored == kind and receiver.parent == "client" and receiver.attr == member
            for kind, receiver in mapping.receivers.items()
        )
    )


def moved_target(kind: str, member: str) -> bool:
    """Project-owned overrides are not SDK mock targets."""
    return client_constructor(kind) or member_receiver(kind, member) in {
        "client",
        "unproven:client",
    }


def demote_member(name: str, stored: str, proofs: AttributeProofs) -> None:
    """Fence dispatch of the replaced member, not the client's identity."""
    owner, _, member = name.rpartition(".")
    if not owner or (
        member != "*" and not replacement(member, stored, proofs.resolver.mapping)
    ):
        return
    kind = proofs.resolver.infer(ast.parse(owner, mode="eval").body, proofs.env)
    # Types do not prove identity, so compatible aliases share this conservative
    # member fence. Review-only values survive conditional joins and loop edges.
    candidates = proofs.resolver.mapping.moved if member == "*" else (member,)
    for candidate in candidates:
        if replacement(candidate, stored, proofs.resolver.mapping) and moved_target(
            kind, candidate
        ):
            proofs.env["@member-write." + candidate] = "unproven:client"


def receiver_kind(node: ast.Attribute, flow: Flow) -> str:
    """Consult member-write history at each individual dispatch position."""
    kind = flow.resolver.infer(node.value, flow.env)
    if "@member-write." + node.attr in flow.env and moved_target(kind, node.attr):
        return "unproven:client"
    return member_receiver(kind, node.attr)


def remember_receiver(node: ast.AST, flow: Flow) -> None:
    """Expose member-specific uncertainty to the position-based CST transformer."""
    target = node if isinstance(node, ast.Attribute) else None
    if (
        isinstance(node, ast.Call)
        and flow.resolver.infer(node.func, flow.env)
        in {"call:builtins.getattr", "call:builtins.hasattr"}
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        target = ast.Attribute(value=node.args[0], attr=node.args[1].value)
    if target is None or "@member-write." + target.attr not in flow.env:
        return
    if receiver_kind(target, flow) == "unproven:client":
        value = target.value
        flow.kinds[
            value.lineno,
            value.col_offset,
            value.end_lineno or value.lineno,
            value.end_col_offset or value.col_offset,
        ] = "unproven:client"


def report_write(context: AttributeContext, node: cst.CSTNode) -> None:
    message = "Review this moved client-member write and its calls together; SDK 2.x moved these members to .v0."
    context.item(node, "mock-target", message)
    context.fixme(node, message)


def attribute_write(
    context: AttributeContext, node: cst.Attribute, parent: cst.CSTNode
) -> bool:
    """Leave mutation targets intact and attach the manual-review diagnostic."""
    if not (
        isinstance(parent, cst.Assign)
        and any(
            contains_target(assignment.target, node) for assignment in parent.targets
        )
        or isinstance(parent, (cst.AnnAssign, cst.AugAssign, cst.Del))
        and contains_target(parent.target, node)
    ):
        return False
    stored = (
        context.kind(parent.value)
        if isinstance(parent, (cst.Assign, cst.AnnAssign)) and parent.value is not None
        else ""
    )
    if not replacement(node.attr.value, stored, context.mapping) or not moved_target(
        context.kind(node.value), node.attr.value
    ):
        return False
    report_write(context, node)
    return True


def contains_target(target: cst.CSTNode, node: cst.Attribute) -> bool:
    """Descend target wrappers only, never a receiver's attribute expressions."""
    if target is node:
        return True
    if isinstance(target, (cst.Tuple, cst.List)):
        return any(contains_target(element, node) for element in target.elements)
    return isinstance(target, (cst.Element, cst.StarredElement)) and contains_target(
        target.value, node
    )


def call_write(
    context: AttributeContext, node: cst.Call, updated: cst.Call
) -> cst.Call:
    """Recognize builtin setattr and object.__setattr__, including proven aliases."""
    kind = context.kind(node.func)
    name = module_name(node.func)
    root, separator, tail = name.partition(".")
    canonical = context.flow.reflective_aliases.get(root, root) + separator + tail
    if (
        not (
            kind == "call:builtins.setattr"
            or canonical in {"object.__setattr__", "builtins.object.__setattr__"}
            and "object" not in context.flow.context.index.builtin_writes
        )
        or len(node.args) != 3
        or any(arg.keyword or arg.star for arg in node.args)
    ):
        return updated
    literal = node.args[1].value
    member = literal.evaluated_value if isinstance(literal, cst.SimpleString) else None
    if (
        isinstance(member, str)
        and replacement(member, context.kind(node.args[2].value), context.mapping)
        and moved_target(context.kind(node.args[0].value), member)
    ):
        report_write(context, node)
    return updated
