# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative URL-field policy shared by constructor and mutation boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import libcst as cst

from .cloud_api_url import cloud_url_identity
from .config_messages import CUSTOM_URL, SHARED_URL_WRITE, UNRESOLVED_CONFIG
from .config_readers import shared_write
from .imports import module_name

if TYPE_CHECKING:
    from .calls import CallContext
    from .models import Mapping


def default_url(value: cst.BaseExpression, mapping: Mapping) -> bool:
    """0.x's literal None and Cloud URLs request the supported default."""
    if isinstance(value, cst.Name) and value.value == "None":
        return True
    literal = value.evaluated_value if isinstance(value, cst.SimpleString) else None
    return isinstance(literal, str) and any(
        cloud_url_identity(literal) == cloud_url_identity(url)
        for url in mapping.cloud_urls
    )


def unresolved_write(
    context: CallContext,
    node: cst.CSTNode,
    value: cst.BaseExpression | None = None,
) -> None:
    asserted = context.options.drop_api_url and not isinstance(value, cst.SimpleString)
    context.item(
        node,
        "config-field" if asserted else "custom-api-url",
        "Review this unchanged API URL write manually after --drop-api-url; SDK 2.x targets Daytona Cloud."
        if asserted
        else CUSTOM_URL
        if isinstance(value, cst.SimpleString)
        else UNRESOLVED_CONFIG,
    )


def rewrite_setattr(
    context: CallContext, call: cst.Call, updated: cst.Call
) -> cst.BaseExpression:
    binding = context.kind(call.func)
    if binding != "call:builtins.setattr" and module_name(call.func) not in {
        "setattr",
        "builtins.setattr",
    }:
        return updated
    if len(call.args) != 3 or any(arg.keyword or arg.star for arg in call.args):
        return updated
    key = call.args[1].value
    if not isinstance(key, cst.SimpleString) or key.evaluated_value not in {
        "api_url",
        "server_url",
    }:
        return updated
    if not context.config_origins.proven(context.position(call.args[0].value)):
        return updated
    if binding != "call:builtins.setattr" or not isinstance(
        call.args[0].value, cst.Name
    ):
        unresolved_write(context, call, call.args[2].value)
        return updated
    if default_url(call.args[2].value, context.mapping):
        if shared_write(context.config_origins, context.position(call.args[0].value)):
            retain_shared_write(context, call)
            return updated
        # setattr returns None; preserve expression syntax even in nested positions.
        return cst.Name("None")
    unresolved_write(context, call, call.args[2].value)
    return updated


def rewrite_statement(
    context: CallContext,
    original: cst.BaseSmallStatement,
    updated: cst.BaseSmallStatement,
) -> cst.BaseSmallStatement | cst.RemovalSentinel:
    if (
        isinstance(original, cst.Expr)
        and isinstance(original.value, cst.Call)
        and isinstance(updated, cst.Expr)
        and isinstance(updated.value, cst.Name)
        and updated.value.value == "None"
    ):
        # Only a safely dropped setattr call becomes None during expression rewriting.
        return cst.RemoveFromParent()
    targets: list[cst.BaseAssignTargetExpression] = []
    value: cst.BaseExpression | None = None
    if isinstance(original, cst.Assign):
        targets = [target.target for target in original.targets]
        value = original.value
    elif isinstance(original, (cst.AnnAssign, cst.AugAssign)):
        targets = [original.target]
        value = original.value
    urls = [
        target
        for target in targets
        if isinstance(target, cst.Attribute)
        and target.attr.value in {"api_url", "server_url"}
        and context.config_origins.proven(context.position(target.value))
    ]
    if any(
        _contains_url(context, target)
        for target in targets
        if not isinstance(target, cst.Attribute)
    ):
        unresolved_write(context, original, value)
        return updated
    if not urls or value is None:
        return updated
    if any(not isinstance(target.value, cst.Name) for target in urls):
        unresolved_write(context, original, value)
        return updated
    if isinstance(original, cst.AugAssign) or not default_url(value, context.mapping):
        unresolved_write(context, original, value)
        return updated
    if any(
        shared_write(context.config_origins, context.position(target.value))
        for target in urls
    ):
        retain_shared_write(context, original)
        return updated
    if isinstance(updated, cst.Assign) and isinstance(original, cst.Assign):
        kept = [
            new
            for old, new in zip(original.targets, updated.targets)
            if old.target not in urls
        ]
        if kept:
            return updated.with_changes(targets=kept)
    # LibCST removes empty lines and emits pass only for emptied suites.
    return cst.RemoveFromParent()


def retain_shared_write(context: CallContext, node: cst.CSTNode) -> None:
    """Keep externally owned URL data while requiring client-config separation."""
    context.item(node, "config-field", SHARED_URL_WRITE)
    context.fixme(node, SHARED_URL_WRITE)


def _contains_url(context: CallContext, target: cst.CSTNode) -> bool:
    if isinstance(target, (cst.Tuple, cst.List)):
        return any(
            element is not None and _contains_url(context, element.value)
            for element in target.elements
        )
    if isinstance(target, cst.StarredElement):
        return _contains_url(context, target.value)
    return (
        isinstance(target, cst.Attribute)
        and context.config_origins.proven(context.position(target.value))
        and target.attr.value
        in {
            "api_url",
            "server_url",
        }
    )
