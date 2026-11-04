# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Call-boundary handling for config URLs and test-double targets."""

from __future__ import annotations

from typing import Protocol

import libcst as cst

from .attributes import AttributeContext, rewrite_lookup, v0_receiver
from .config_constructor import review_constructor
from .config_members import rewrite_config
from .config_origins import ConfigOrigins
from .imports import mock_target, mock_target_kind, module_name, moved_module
from .mock_inference import SPEC_REVIEWS
from .mock_module_factories import review_module_factory
from .mock_proofs import (
    patch_helper,
    plausible_target,
    rewrite_configuration,
    rewrite_object,
)
from .subclasses import (
    client_constructor,
    member_receiver,
)


class CallContext(AttributeContext, Protocol):
    config_origins: ConfigOrigins
    sdk_mock_found: bool

    def position(self, node: cst.CSTNode) -> tuple[int, int]: ...


def target_literal(literal: cst.SimpleString, target: str) -> cst.SimpleString:
    """Preserve delimiter/prefix style while encoding the replacement target safely."""
    if not target.isprintable():
        return literal.with_changes(value=repr(target))
    content = target
    if "r" not in literal.prefix.lower():
        content = content.replace("\\", "\\\\").replace(
            literal.quote[0], "\\" + literal.quote[0]
        )
        content = content.replace("\n", "\\n").replace("\r", "\\r")
    return literal.with_changes(
        value=literal.prefix + literal.quote + content + literal.quote
    )


def rewrite_call(
    context: CallContext, original: cst.Call, updated: cst.Call
) -> cst.Call:
    updated = rewrite_lookup(context, original, updated)
    updated = rewrite_configuration(context, original, updated)
    if message := SPEC_REVIEWS.get(context.kind(original)):
        context.item(original, "mock-target", message)
        return updated
    binding = context.kind(original.func)
    review_constructor(context, original)
    name = (
        binding.split(":", 1)[1]
        if binding.startswith(("call:", "module:"))
        else module_name(original.func)
    )
    if name in ("importlib.import_module", "__import__"):
        if not original.args or not isinstance(
            original.args[0].value, cst.SimpleString
        ):
            context.item(
                original,
                "dynamic-import",
                "Dynamic import cannot be resolved; review its Daytona namespace.",
            )
        else:
            value = original.args[0].value.evaluated_value
            if (
                isinstance(value, str)
                and value.split(".")[0] in context.mapping.modules
                and not context.flow.context.index.owns(value)
            ):
                context.item(
                    original,
                    "dynamic-import",
                    "Review dynamic Daytona import against the v0 namespace.",
                )
                target = moved_module(
                    value, context.mapping, context.flow.context.index
                )
                if target != value:
                    args = list(updated.args)
                    args[0] = args[0].with_changes(
                        value=target_literal(original.args[0].value, target)
                    )
                    updated = updated.with_changes(args=args)
    if helper := patch_helper(binding):
        args = list(updated.args)
        target_index = next(
            (
                index
                for index, arg in enumerate(original.args)
                if arg.keyword is not None and arg.keyword.value == "target"
            ),
            next(
                (
                    index
                    for index, arg in enumerate(original.args)
                    if arg.keyword is None and not arg.star
                ),
                None,
            ),
        )
        target_value = args[target_index].value if target_index is not None else None
        if target_index is not None and review_module_factory(
            context, original, target_index
        ):
            return updated
        if target_index is not None and helper in {
            "patch.object",
            "patch.multiple",
            "setattr",
        }:
            members = [
                arg.keyword.value for arg in original.args if arg.keyword is not None
            ]
            if helper != "patch.multiple" and target_index + 1 < len(original.args):
                literal = original.args[target_index + 1].value
                if isinstance(literal, cst.SimpleString) and isinstance(
                    literal.evaluated_value, str
                ):
                    members.append(literal.evaluated_value)
            if not plausible_target(
                context, original.args[target_index].value, members
            ):
                # setattr's single dotted-string form patches a member, not a module.
                if helper != "setattr" or not isinstance(
                    target_value, cst.SimpleString
                ):
                    return updated
        if target_index is not None and (helper == "patch.multiple"):
            # patch.multiple's own options configure every patch; they are not members.
            members = [
                arg
                for index, arg in enumerate(original.args)
                if index != target_index
                and not (
                    arg.keyword is not None
                    and arg.keyword.value
                    in {"spec", "create", "spec_set", "autospec", "new_callable"}
                )
            ]
            receiver = context.kind(original.args[target_index].value)
            if members and not any(arg.star for arg in original.args):
                kinds = [
                    member_receiver(receiver, arg.keyword.value)
                    for arg in members
                    if arg.keyword is not None
                    and arg.keyword.value in context.mapping.moved
                ]
                if len(kinds) == len(members) and all(
                    kind == "client" for kind in kinds
                ):
                    args[target_index] = args[target_index].with_changes(
                        value=v0_receiver(args[target_index].value)
                    )
                    return updated.with_changes(args=args)
                if receiver == "client.v0":
                    return updated
            if any(
                arg.keyword is not None and arg.keyword.value in context.mapping.moved
                for arg in members
            ) and any(
                arg.star
                or arg.keyword is None
                or arg.keyword.value not in context.mapping.moved
                for arg in members
            ):
                message = "Mock target mixes moved and non-moved or unresolved members; split this patch.multiple by hand to patch the v0 client separately."
                context.item(original, "mock-target", message)
                context.fixme(original, message)
                return updated
            context.item(
                original,
                "mock-target",
                "Review unresolved mock target against the v0 namespace.",
            )
            return updated
        if target_index is not None and (helper == "patch.object"):
            if rewritten := rewrite_object(context, original, updated):
                return rewritten
        if target_index is not None and isinstance(target_value, cst.SimpleString):
            value = target_value.evaluated_value
            if isinstance(value, str):
                context.sdk_mock_found |= value.split(".", 1)[
                    0
                ] in context.mapping.modules and not context.flow.context.index.owns(
                    value
                )
                target, manual = mock_target(
                    value, context.mapping, context.flow.context.index
                )
                trusted = not binding.startswith(("unbound:", "module:unbound."))
                if target != value and trusted:
                    args[target_index] = args[target_index].with_changes(
                        value=target_literal(target_value, target)
                    )
                if target != value and not trusted:
                    context.item(
                        original,
                        "mock-target",
                        "Review unresolved mock helper before moving this SDK target to the v0 namespace.",
                    )
                elif manual:
                    context.item(
                        original,
                        "mock-target",
                        "Mock target references the root-client constructor; configure return_value.v0.<member> instead."
                        if client_constructor(
                            mock_target_kind(
                                value, context.mapping, context.flow.context.index
                            )
                        )
                        else "Mock target references a moved root-client member; patch the v0 client instead.",
                    )
        else:
            context.item(
                original,
                "mock-target",
                "Review unresolved mock target against the v0 namespace.",
            )
        updated = updated.with_changes(args=args)
    return rewrite_config(context, original, updated)
