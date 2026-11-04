# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Receiver-proven client member routing and unproven-call diagnostics."""

from __future__ import annotations

from typing import Protocol

import libcst as cst

from .flow import Flow
from .lexical import single_line
from .mock_inference import SPEC_REVIEWS
from .models import ItemKind, Mapping, TransformOptions
from .module_proofs import V0_SUBMODULES, module_review
from .resolution import looks_like_client
from .subclasses import member_receiver


class AttributeContext(Protocol):
    flow: Flow
    mapping: Mapping
    options: TransformOptions

    def kind(self, node: cst.CSTNode) -> str: ...
    def mock_linked(self, node: cst.CSTNode) -> bool: ...
    def item(self, node: cst.CSTNode, kind: ItemKind, message: str) -> None: ...
    def fixme(self, node: cst.CSTNode, message: str) -> None: ...


def v0_receiver(receiver: cst.BaseExpression) -> cst.Attribute:
    """Keep low-precedence receiver expressions grouped before member access."""
    if not receiver.lpar and not isinstance(
        receiver, (cst.Name, cst.Attribute, cst.Call, cst.Subscript)
    ):
        grouped = receiver.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])
        return cst.Attribute(grouped, cst.Name("v0"))
    return cst.Attribute(receiver, cst.Name("v0"))


def rewrite_attribute(
    context: AttributeContext, original: cst.Attribute, updated: cst.Attribute
) -> cst.BaseExpression:
    """Route proven clients; annotate uncertain decorated factories without trusting them."""
    if context.options.mode != "upgrade":
        return updated
    member = original.attr.value
    kind = member_receiver(context.kind(original.value), member)
    if member in context.mapping.moved and kind.startswith("fixture:"):
        message = SPEC_REVIEWS.get(kind.removeprefix("fixture:"))
        if message:
            context.item(original, "mock-target", message)
            return updated
    if (
        kind == "local-double:client"
        and member in context.mapping.moved
        or kind == "local-double:module"
        and member in V0_SUBMODULES
    ):
        return updated.with_changes(value=v0_receiver(updated.value))
    if (
        module_review(kind)
        and not kind.startswith("module:")
        and member not in (*context.mapping.root_imports, "v0", "v2")
        and not (member.startswith("__") and member.endswith("__"))
    ):
        receiver = single_line(cst.Module([]).code_for_node(original.value))
        message = f"review `{receiver}.{member}`: its receiver may be the Daytona module; if so, use `{receiver}.v0.{member}` (SDK 2.x moved 0.x module paths to daytona.v0)"
        context.item(original, "fixme", message)
        context.fixme(original, message)
        return updated
    if kind in ("module:daytona", "module:daytona_sdk") and (
        member.startswith("__") and member.endswith("__")
    ):
        return updated
    if kind == "client" and member in context.mapping.moved:
        # Already namespaced code stays untouched, including detached members.
        if isinstance(original.value, cst.Attribute) and original.value.attr.value in (
            "v0",
            "v2",
        ):
            return updated
        return updated.with_changes(value=v0_receiver(updated.value))
    if kind in ("module:daytona", "module:daytona_sdk") and (
        member not in (*context.mapping.root_imports, "v0", "v2")
    ):
        return updated.with_changes(value=v0_receiver(updated.value))
    if (
        member in context.mapping.moved
        and kind in ("", "unproven:client", "fn:unproven:client")
        and (context.flow.found or kind in ("unproven:client", "fn:unproven:client"))
    ):
        receiver = single_line(cst.Module([]).code_for_node(original.value))
        if (
            kind in ("unproven:client", "fn:unproven:client")
            or looks_like_client(receiver)
        ) and ".v0" not in receiver:
            message = f"if `{receiver}` is a Daytona client, use `{receiver}.v0.{member}(...)` (SDK 2.x moved client methods to .v0)"
            context.item(original, "fixme", message)
            context.fixme(original, message)
    return updated


def rewrite_lookup(
    context: AttributeContext, original: cst.Call, updated: cst.Call
) -> cst.Call:
    """Route literal getattr/hasattr arguments only for binding-proven builtins."""
    binding = context.kind(original.func)
    if (
        context.options.mode != "upgrade"
        or binding not in {"call:builtins.getattr", "call:builtins.hasattr"}
        or binding.removeprefix("call:builtins.")
        in context.flow.context.index.builtin_writes
        or len(original.args) not in (2, 3)
        or binding == "call:builtins.hasattr"
        and len(original.args) != 2
        or any(arg.keyword is not None or arg.star for arg in original.args)
    ):
        return updated
    name = original.args[1].value
    receiver = original.args[0].value
    kind = context.kind(receiver)
    member = (
        name.evaluated_value
        if isinstance(name, (cst.SimpleString, cst.ConcatenatedString))
        else None
    )
    if member is None:
        if kind == "client" or kind.startswith(("client-subclass:", "self-subclass:")):
            text = cst.Module([]).code_for_node(receiver)
            message = f"review dynamic member lookup on `{text}`; SDK 2.x moved client methods to .v0"
            context.item(original, "fixme", message)
            context.fixme(original, message)
        return updated
    if member not in context.mapping.moved:
        return updated
    kind = member_receiver(kind, member)
    if kind == "client":
        args = list(updated.args)
        args[0] = args[0].with_changes(value=v0_receiver(args[0].value))
        return updated.with_changes(args=args)
    if kind in ("", "unproven:client", "fn:unproven:client") and (
        context.flow.found or kind in ("unproven:client", "fn:unproven:client")
    ):
        text = cst.Module([]).code_for_node(receiver)
        if (
            kind in ("unproven:client", "fn:unproven:client") or looks_like_client(text)
        ) and ".v0" not in text:
            message = f"if `{text}` is a Daytona client, use `{text}.v0.{member}(...)` (SDK 2.x moved client methods to .v0)"
            context.item(original, "fixme", message)
            context.fixme(original, message)
    return updated
