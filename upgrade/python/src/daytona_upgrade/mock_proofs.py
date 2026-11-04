# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Mock-library identities and precision-first target eligibility."""

from __future__ import annotations

import libcst as cst

from .attributes import AttributeContext, v0_receiver
from .imports import mock_target_kind
from .mock_configuration import configuration_collision
from .resolution import looks_like_client
from .subclasses import client_constructor, member_receiver


def rewrite_configuration(
    context: AttributeContext, original: cst.Call, updated: cst.Call
) -> cst.Call:
    """Move literal instance-mock keys together with the members they configure."""
    if not (
        isinstance(original.func, cst.Attribute)
        and original.func.attr.value == "configure_mock"
        and context.kind(original.func.value) == "mock:client"
    ):
        return updated
    args: list[cst.Arg] = []
    keys: list[str] = []
    for argument in updated.args:
        if argument.keyword is not None:
            name = argument.keyword.value
            keys.append(name)
            if name in context.mapping.moved:
                argument = argument.with_changes(
                    keyword=None,
                    equal=cst.MaybeSentinel.DEFAULT,
                    star="**",
                    value=cst.Dict(
                        [
                            cst.DictElement(
                                cst.SimpleString('"v0.' + name + '"'), argument.value
                            )
                        ]
                    ),
                )
        elif argument.star == "**" and isinstance(argument.value, cst.Dict):
            elements: list[cst.BaseDictElement] = []
            for element in argument.value.elements:
                if not (
                    isinstance(element, cst.DictElement)
                    and isinstance(
                        element.key, (cst.SimpleString, cst.ConcatenatedString)
                    )
                    and isinstance(element.key.evaluated_value, str)
                ):
                    break
                name = element.key.evaluated_value
                keys.append(name)
                if name.split(".", 1)[0] in context.mapping.moved:
                    key = element.key
                    literal = (
                        key.with_changes(
                            value=key.prefix
                            + key.quote
                            + "v0."
                            + key.raw_value
                            + key.quote
                        )
                        if isinstance(key, cst.SimpleString)
                        else cst.SimpleString(repr("v0." + name))
                    )
                    element = element.with_changes(key=literal)
                elements.append(element)
            if len(elements) != len(argument.value.elements):
                break
            argument = argument.with_changes(
                value=argument.value.with_changes(elements=elements)
            )
        else:
            break
        args.append(argument)
    if len(args) != len(updated.args):
        context.item(
            original,
            "mock-target",
            "Mock configure_mock keys are unresolved; configure moved members under v0 and review subsequent calls on this mock manually.",
        )
        return updated
    if configuration_collision(keys, context.mapping.moved):
        context.item(
            original,
            "mock-target",
            "Mock configure_mock keys collide after v0 routing; resolve duplicate targets and review subsequent calls on this mock manually.",
        )
        return updated
    return updated.with_changes(args=args)


def patch_helper(binding: str) -> str:
    """Recognize canonical mock imports/fixtures, never an arbitrary patch name."""
    for prefix in (
        "call:unittest.mock.",
        "module:unittest.mock.",
        "call:mock.",
        "module:mock.",
        "module:pytest_mock.MockerFixture.",
        "module:pytest.MonkeyPatch.",
        "module:unbound.",
        "unbound:",
    ):
        if binding.startswith(prefix):
            helper = binding[len(prefix) :]
            if helper in {"patch", "patch.object", "patch.multiple", "setattr"}:
                return helper
    return ""


def plausible_kind(kind: str, context: AttributeContext) -> bool:
    """SDK classes, instances and linked uncertain values warrant mock review."""
    return (
        client_constructor(kind)
        or kind.startswith(("mock:", "mock-class:"))
        or kind in {"client", "unproven:client", "fn:unproven:client"}
        or kind.startswith(("client-subclass:", "self-subclass:"))
        or kind.startswith("module:")
        and kind.split(":", 1)[1].split(".")[0] in context.mapping.modules
        and not context.flow.context.index.owns(kind.split(":", 1)[1])
    )


def plausible_target(
    context: AttributeContext, target: cst.BaseExpression, members: list[str]
) -> bool:
    """Resolve object targets and module members before applying name-only policy."""
    kind = context.kind(target)
    if isinstance(target, cst.SimpleString) and isinstance(target.evaluated_value, str):
        kind = mock_target_kind(
            target.evaluated_value, context.mapping, context.flow.context.index
        )
    if plausible_kind(kind, context) or context.mock_linked(target):
        return True
    if kind.startswith(("module:", "local-module:")):
        module = kind.split(":", 1)[1]
        return any(
            plausible_kind(
                mock_target_kind(
                    module + "." + member, context.mapping, context.flow.context.index
                ),
                context,
            )
            for member in members
        )
    return (
        not kind
        and context.flow.found
        and looks_like_client(cst.Module([]).code_for_node(target))
    )


def rewrite_object(
    context: AttributeContext, original: cst.Call, updated: cst.Call
) -> cst.Call | None:
    """Route instance targets; explain why class-level moved members need review."""
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
    if target_index is None:
        return None
    member_index = next(
        (
            index
            for index, arg in enumerate(original.args)
            if arg.keyword is not None and arg.keyword.value == "attribute"
        ),
        target_index + 1 if target_index + 1 < len(original.args) else None,
    )
    if member_index is None or any(
        arg.star for arg in original.args[: max(target_index, member_index) + 1]
    ):
        return None
    literal = original.args[member_index].value
    member = (
        literal.evaluated_value
        if isinstance(literal, (cst.SimpleString, cst.ConcatenatedString))
        else None
    )
    if not isinstance(member, str) or member not in context.mapping.moved:
        return None
    receiver = context.kind(original.args[target_index].value)
    kind = (
        receiver
        if receiver == "mock-class:client"
        else member_receiver(receiver, member)
    )
    if kind == "client":
        args = list(updated.args)
        args[target_index] = args[target_index].with_changes(
            value=v0_receiver(args[target_index].value)
        )
        return updated.with_changes(args=args)
    if kind == "client.v0":
        return updated
    if client_constructor(kind) or kind == "mock-class:client":
        context.item(
            original,
            "mock-target",
            "Mock target references a moved root-client member; patch the v0 client instead.",
        )
        return updated
    return None
